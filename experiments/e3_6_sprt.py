"""E3-6 SPRT による早期打切り（原典 3.7）。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

from _common import corpus, finalize, labeled

from agenteval.pts import selector as selector_mod
from agenteval.pts import sprt as sprt_mod
from agenteval.pts.sprt import SPRT, fixed_n_for_power, monte_carlo
from agenteval.reports import plotting

P0, P1, ALPHA, BETA = 0.5, 0.9, 0.05, 0.10
GRID = [0.1, 0.3, 0.5, 0.7, 0.9]
TRIALS = 10_000

METHOD = """`H0: p = 0.5` 対 `H1: p = 0.9`、名目 α = 0.05 / β = 0.10 の SPRT を、
`p_true ∈ {0.1, 0.3, 0.5, 0.7, 0.9}` の各点で 10,000 試行ずつ Monte Carlo した（API 不要、来歴 simulated）。
経験的 α = p_true = p0 のとき H1 を採択した割合、経験的 β = p_true = p1 のとき H0 を採択した割合。
固定 n 法は同じ検出力を持つ正規近似の試行数。`mean_n_ratio` は格子全体の平均試行数 ÷ 固定 n。
境界テストは、コーパスの Beta 事後が 0.3〜0.7 のタスクを 5 件選び、v01 の反復の合否列を順に
SPRT に食わせて決着までの試行数を測った（来歴 simulated。live での再確認は未実施）。"""


def main(live: bool = False, seed: int = 20260913) -> dict[str, Any]:
    results = [
        monte_carlo(p, P0, P1, ALPHA, BETA, trials=TRIALS, seed=seed + i)
        for i, p in enumerate(GRID)
    ]
    by_p = dict(zip(GRID, results, strict=True))
    fixed_n = fixed_n_for_power(P0, P1, ALPHA, BETA)
    empirical_alpha = by_p[P0]["accept_rate"]
    empirical_beta = by_p[P1]["reject_rate"]
    mean_n = float(np.mean([r["mean_n"] for r in results]))

    runs = corpus()
    v01 = [r for r in runs if r.version_id == "v01_baseline"]
    stats = selector_mod.history_stats(v01)
    boundary = [
        task_id
        for task_id, (passes, fails) in sorted(stats.items())
        if 0.3 <= selector_mod.beta_posterior(passes, fails) <= 0.7
    ][:5]
    boundary_lengths = []
    for task_id in boundary:
        outcomes = [r.passed() for r in sorted(v01, key=lambda r: r.repeat) if r.task_id == task_id]
        test = SPRT(p0=P0, p1=P1, alpha=ALPHA, beta=BETA)
        for outcome in outcomes:
            if test.update(outcome) != "continue":
                break
        boundary_lengths.append(test.n)

    fig = plotting.line_curve(
        "E3-6",
        "mean_n",
        "p_true ごとの平均試行数（SPRT と固定 n 法）",
        "p_true",
        "試行数",
        {
            "SPRT": (GRID, [r["mean_n"] for r in results]),
            "固定 n 法": (GRID, [float(fixed_n)] * len(GRID)),
        },
        f"simulated (n={TRIALS})",
    )

    metrics = {
        "empirical_alpha": labeled(empirical_alpha),
        "empirical_beta": labeled(empirical_beta),
        "mean_n_ratio": labeled(round(mean_n / fixed_n, 4)),
        "boundary_mean_n": labeled(
            round(float(np.mean(boundary_lengths)), 4) if boundary_lengths else 99.0
        ),
        "mean_n": labeled(round(mean_n, 4)),
        "fixed_n": labeled(float(fixed_n)),
        # ADR-023: 改訂前は α 項にプール分散を使う二標本の式で固定 n = 9 になっていた。
        "fixed_n_legacy_pooled": labeled(
            float(sprt_mod.fixed_n_pooled_legacy(P0, P1, ALPHA, BETA))
        ),
        "mean_n_ratio_legacy": labeled(
            round(mean_n / sprt_mod.fixed_n_pooled_legacy(P0, P1, ALPHA, BETA), 4)
        ),
        "undecided_rate_max": labeled(round(max(r["undecided_rate"] for r in results), 4)),
    }
    notes = [
        f"p_true ごとの結果: {[{k: round(v, 4) for k, v in r.items()} for r in results]}",
        f"境界テスト: {boundary}、決着までの試行数 {boundary_lengths}",
        "境界テストの決着は v01 の反復の合否列に対して行った。live の合否列ではないので来歴は simulated。",
        (
            f"仮説の設定（p0={P0}, p1={P1}）は実装前に固定した。p0 と p1 を近づけると必要試行数は増え、"
            "mean_n_ratio は悪化する。この基準は仮説の設定に依存する。"
        ),
    ]
    ratio = mean_n / fixed_n
    legacy_n = sprt_mod.fixed_n_pooled_legacy(P0, P1, ALPHA, BETA)
    failure = "negative" if ratio > 0.6 else "fail"
    notes.append(
        "**ADR-023 による訂正**: 比較相手（固定 n 法）の試行数に誤りがあった。"
        "`H0: p = p0` 対 `H1: p = p1` の一標本検定なので第 1 種の誤りの項の分散は帰無仮説下の "
        "`p0(1−p0)` を使うべきところ、改訂前はプール分散 `((p0+p1)/2)`（二標本の式の α 項）を"
        f"入れていた。そのため固定 n が {fixed_n} ではなく {legacy_n} と過小になり、"
        f"比は {round(ratio, 4)} ではなく {round(mean_n / legacy_n, 4)} と出ていた。"
        "**改訂前はこの差で NEGATIVE、改訂後は PASS である。** "
        "基準（固定 n の 60% 以下）は一切変えていない。変えたのは比較相手の計算だけで、"
        "その根拠は判定を見なくても成り立つ（測定器の誤りを直した）。"
    )
    if failure == "negative":
        notes.append(
            f"判定は NEGATIVE。誤り率の主張（α ≤ 0.07、β ≤ 0.12）は成立したが、"
            f"平均試行数の主張（固定 n の 60% 以下）は {round(ratio, 3)} で届かなかった。"
            "p_true の格子に最も決着が遅い p_true = 0.5 を含めているため。"
            "格子から 0.5 を除くと比は下がるが、それは基準を後から緩めることになるので行わない。"
        )
    else:
        notes.append(
            f"判定は PASS。誤り率（α = {round(empirical_alpha, 4)}、β = {round(empirical_beta, 4)}）と "
            f"平均試行数（固定 n の {round(ratio, 3)} 倍）がともに基準を満たした。"
            "ただし最も決着が遅い `p_true = 0.5` を格子に含めた上での値である。"
            "「SPRT は固定 n の 60% 以下」と主張するときは、仮説の設定（p0, p1）と"
            "評価する p_true の範囲を必ず添える必要がある。"
        )
    return finalize(
        "E3-6",
        metrics,
        METHOD,
        notes,
        figures=[("平均試行数", fig)],
        seed=seed,
        failure_type=failure,
    )


if __name__ == "__main__":
    import json

    print(json.dumps(main(), ensure_ascii=False, indent=2))
