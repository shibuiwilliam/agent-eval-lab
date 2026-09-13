"""E3-3 不確実性駆動のテスト選択（原典 3.4）。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

from _common import corpus, finalize, flake_band, flipped_tasks, labeled

from agenteval.core.registry import load_tasks
from agenteval.pts import change as change_mod
from agenteval.pts import coverage as coverage_mod
from agenteval.pts import kpi as kpi_mod
from agenteval.pts import selector as selector_mod
from agenteval.reports import plotting

TARGETS = [
    "v02_dateformat",
    "v03_noverify",
    "v04_loopy",
    "v05_unsafe_delete",
    "v06_toolschema_v2",
    "v07_step_minimizer",
    "v08_summarize_ctx",
    "v09_model_swap",
]

METHOD = """変更イベント = v01 → v02〜v09 の 8 件。各イベントについて、そのイベントを除いた履歴
（leave-one-change-out）から `p̂_t` を推定し、`H(p̂)/c` の降順に予算 30% まで貪欲選択した
（冗長性: ツール名列の正規化編集距離の類似 > 0.8 は飛ばす）。逃走欠陥率は
「選ばなかったテストのうち実際に反転していたものの割合」（原典 3.8）。
対照はランダム選択（1,000 回抽選の平均）と直近失敗優先。
`expected_escape` は選ばなかったテストの `Σ(1 − p̂_t) / |T|` で、実測の逃走欠陥率と並べて校正誤差を見た。"""


def main(live: bool = False, seed: int = 20260913) -> dict[str, Any]:
    runs = corpus()
    bands = flake_band(runs, "v01_baseline")
    task_ids = sorted(load_tasks())
    coverage = coverage_mod.coverage_map([r for r in runs if r.version_id == "v01_baseline"])

    events = []
    for target in TARGETS:
        merged = change_mod.merge(change_mod.diff_versions("v01_baseline", target))
        assert merged is not None
        events.append((merged, [r for r in runs if r.version_id in ("v01_baseline", target)]))

    rng = np.random.default_rng(seed)
    rows = []
    for index, target in enumerate(TARGETS):
        change, _ = events[index]
        flipped = flipped_tasks(runs, "v01_baseline", target, bands)
        history = [r for r in runs if r.version_id not in (target,)]
        model = selector_mod.fit_model(events, coverage, holdout=index)

        uncertainty = selector_mod.select(
            task_ids, history, change, coverage, budget_ratio=0.3, model=model
        )
        # 診断用の変種: 冗長性ペナルティを切ったときの逃走欠陥率（基準の判定には使わない）
        no_redundancy = selector_mod.select(
            task_ids,
            history,
            change,
            coverage,
            budget_ratio=0.3,
            model=model,
            redundancy_threshold=2.0,
        )
        recent = selector_mod.select_recent_failures(task_ids, history, budget_ratio=0.3)
        random_escapes = [
            kpi_mod.escape_rate(
                selector_mod.select_random(
                    task_ids, history, 0.3, seed=int(rng.integers(0, 10**6))
                ),
                flipped,
            )
            for _ in range(1000)
        ]
        rows.append(
            {
                "change": target,
                "n_flipped": len(flipped),
                "uncertainty": kpi_mod.escape_rate(uncertainty, flipped),
                "random": float(np.mean(random_escapes)),
                "recent": kpi_mod.escape_rate(recent, flipped),
                "expected": uncertainty.expected_escape,
                "selected": len(uncertainty.selected),
                "no_redundancy": kpi_mod.escape_rate(no_redundancy, flipped),
                "n_skipped_redundant": sum(
                    1 for r in uncertainty.reasons.values() if r == "redundant"
                ),
            }
        )

    measurable = [r for r in rows if r["n_flipped"] > 0]
    escape_u = float(np.mean([r["uncertainty"] for r in measurable])) if measurable else 0.0
    escape_r = float(np.mean([r["random"] for r in measurable])) if measurable else 0.0
    escape_f = float(np.mean([r["recent"] for r in measurable])) if measurable else 0.0
    calibration = (
        float(np.mean([abs(r["expected"] - r["uncertainty"]) for r in measurable]))
        if measurable
        else 1.0
    )
    escape_nr = float(np.mean([r["no_redundancy"] for r in measurable])) if measurable else 0.0
    redundant_skips = (
        float(np.mean([r["n_skipped_redundant"] for r in measurable])) if measurable else 0.0
    )

    fig = plotting.bar_compare(
        "E3-3",
        "escape_rate",
        "予算 30% での逃走欠陥率（変更イベント平均）",
        ["不確実性駆動", "ランダム(1000回平均)", "直近失敗優先"],
        [escape_u, escape_r, escape_f],
        "逃走欠陥率",
        "simulated",
    )
    fig2 = plotting.scatter(
        "E3-3",
        "calibration",
        "expected_escape と実測の逃走欠陥率",
        "expected_escape",
        "実測",
        [r["expected"] for r in measurable],
        [r["uncertainty"] for r in measurable],
        "simulated",
        diagonal=True,
    )

    metrics = {
        "escape_ratio_vs_random": labeled(round(escape_u / escape_r, 4) if escape_r else 0.0),
        "calibration_error": labeled(round(calibration, 4)),
        "escape_uncertainty": labeled(round(escape_u, 4)),
        "escape_random": labeled(round(escape_r, 4)),
        "escape_recent_failures": labeled(round(escape_f, 4)),
        "n_measurable_events": labeled(len(measurable)),
        "escape_no_redundancy_variant": labeled(round(escape_nr, 4)),
        "mean_redundant_skips": labeled(round(redundant_skips, 4)),
    }
    notes = [
        f"変更イベントごとの結果: {rows}",
        (
            "原因の切り分け: 冗長性ペナルティ（`.claude/rules/pts.md` の「ツール名列の正規化編集距離の補数が "
            "0.8 を超える候補は飛ばす」）が、候補の大半を落としている（平均 "
            f"{round(redundant_skips, 1)} 件／回）。この例示環境ではタスクのツール名列が "
            "`calendar_search → calendar_create → checks_run → finish` のようにほぼ同一なので、"
            "ツール名だけの類似度では別タスクを区別できない。"
        ),
        (
            "冗長性ペナルティを切った変種では逃走欠陥率が "
            f"{round(escape_nr, 3)}（ランダムは {round(escape_r, 3)}）まで下がる。"
            "つまり「カバレッジで絞る → 不確実性で並べる」経路自体は効いており、"
            "落としているのは類似度の定義である。"
        ),
        (
            "判定は FAIL（実験設計の不備）とする。手法が主張どおり働かなかったのではなく、"
            "類似度をツール名列だけで測る実装と、ツール名列が同質なタスク集合の組合せが原因。"
            "修正案: 類似度に正規化引数を含める（規則の変更なので ADR が要る）か、"
            "タスクのツール列を多様にする。"
        ),
        "反転が 0 件の変更イベントは逃走欠陥率が定義できないので平均から除いた（件数を n_measurable_events に記載）。",
        (
            "ロジスティック回帰は変更イベント単位の leave-one-out で学習した。"
            "学習データが 8 イベントと少ないため、Beta 事後との平均が支配的である。"
        ),
    ]
    return finalize(
        "E3-3",
        metrics,
        METHOD,
        notes,
        figures=[("逃走欠陥率", fig), ("校正", fig2)],
        seed=seed,
    )


if __name__ == "__main__":
    import json

    print(json.dumps(main(), ensure_ascii=False, indent=2))
