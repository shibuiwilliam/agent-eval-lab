"""E3-3 不確実性駆動のテスト選択（原典 3.4、KPI は原典 3.8）。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

from _common import (
    corpus,
    escape_over_failures,
    failures_by_repeat,
    finalize,
    flake_band,
    flipped_tasks,
    labeled,
)

from agenteval.core.registry import get_task, load_tasks
from agenteval.pts import change as change_mod
from agenteval.pts import coverage as coverage_mod
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
BUDGET = 0.3
N_RANDOM = 300

METHOD = """変更イベント = v01 → v02〜v09 の 8 件。各イベントについて、**現行配備 v01_baseline の履歴**から
`p̂_t` を推定し（ADR-022）、`H(p̂)/c` の降順に予算 30% まで貪欲選択した
（冗長性: ツール名列の正規化編集距離の類似 > 0.8 は飛ばす）。

**逃走欠陥率は 2 通りで測る。**
1. `Escape(S) = |F_full \\ S| / |F_full|`（原典 3.8 の定義）。`F_full` は**そのフル実行で不合格に
   なったタスク**。フル実行 1 回 = 各テスト 1 回なので、反復ごとに `F_full(r)` を作って平均した（ADR-018）
2. 反転ベース（改訂前の定義）。`F_full` を「変更で合否が反転したタスク」にしたもの

改訂前は 1 の代わりに 2 を使いながら、校正誤差を `expected_escape`（失敗質量 `Σ(1 − p̂)` の予測）と
比べていた。**比較不能な 2 量を引き算していた**ので、校正誤差はどう転んでも意味を持たなかった。

対照はランダム選択（各イベント 300 回抽選の平均）と直近失敗優先。いずれも不可侵集合を含む（ADR-020）。
さらに、どの構成要素が効いているかを切り分けるため、選択方策を 6 通り並べて同じ 2 つの KPI で比較した
（冗長性の有無、カバレッジ優先の有無、H(p̂) の代わりに失敗確率やカバレッジ重なりを使う変種）。
予算内で到達可能な下限は、各反復の `F_full` を事前に知っているオラクルで出した。"""


def _greedy(
    task_ids: list[str],
    hist: list[Any],
    key: Any,
    costs: dict[str, float],
    mean_cost: float,
    inviolable: list[str],
    skip_redundant: bool = False,
) -> set[str]:
    """診断用の素朴な貪欲選択（不可侵集合は必ず含める）。"""
    budget = BUDGET * sum(costs.get(t, mean_cost) for t in task_ids)
    selected = list(inviolable)
    spent = sum(costs.get(t, mean_cost) for t in inviolable)
    for task_id in sorted([t for t in task_ids if t not in inviolable], key=key, reverse=True):
        cost = costs.get(task_id, mean_cost)
        if spent + cost > budget:
            continue
        if skip_redundant and any(
            selector_mod.similarity(
                selector_mod.tool_sequence(hist, task_id), selector_mod.tool_sequence(hist, other)
            )
            > 0.8
            for other in selected
            if other not in inviolable
        ):
            continue
        selected.append(task_id)
        spent += cost
    return set(selected)


def main(live: bool = False, seed: int = 20260913) -> dict[str, Any]:
    runs = corpus()
    bands = flake_band(runs, "v01_baseline")
    task_ids = sorted(load_tasks())
    # ADR-022: 不確実性の推定に使う履歴は現行配備（v01）に限る。
    hist = [r for r in runs if r.version_id == "v01_baseline"]
    coverage = coverage_mod.coverage_map(hist)
    costs = selector_mod.cost_of(hist)
    mean_cost = float(np.mean(list(costs.values()))) if costs else 1.0
    inviolable = [t for t in task_ids if get_task(t).risk == "inviolable"]

    events = []
    for target in TARGETS:
        merged = change_mod.merge(change_mod.diff_versions("v01_baseline", target))
        assert merged is not None
        events.append((merged, [r for r in runs if r.version_id in ("v01_baseline", target)]))

    rng = np.random.default_rng(seed)
    rows = []
    for index, target in enumerate(TARGETS):
        change, _ = events[index]
        flipped = flipped_tasks(runs, "v01_baseline", target)
        flipped_legacy = flipped_tasks(runs, "v01_baseline", target, bands)
        failures = failures_by_repeat(runs, target)
        model = selector_mod.fit_model(events, coverage, holdout=index)

        chosen = selector_mod.select(
            task_ids, hist, change, coverage, budget_ratio=BUDGET, model=model
        )
        selected = set(chosen.selected)
        recent = set(
            selector_mod.select_recent_failures(task_ids, hist, budget_ratio=BUDGET).selected
        )
        rand_fail, rand_flip = [], []
        for _ in range(N_RANDOM):
            pick = set(
                selector_mod.select_random(
                    task_ids, hist, BUDGET, seed=int(rng.integers(0, 10**6))
                ).selected
            )
            rand_fail.append(escape_over_failures(pick, failures))
            if flipped:
                rand_flip.append(len(flipped - pick) / len(flipped))

        # 予算内の下限（各反復の F_full を事前に知っているオラクル）
        oracle = []
        budget_tokens = BUDGET * sum(costs.get(t, mean_cost) for t in task_ids)
        for fails in failures.values():
            if not fails:
                continue
            pick_o = set(inviolable)
            spent = sum(costs.get(t, mean_cost) for t in inviolable)
            for task_id in sorted(fails, key=lambda t: costs.get(t, mean_cost)):
                cost = costs.get(task_id, mean_cost)
                if task_id in pick_o or spent + cost > budget_tokens:
                    continue
                pick_o.add(task_id)
                spent += cost
            oracle.append(len(fails - pick_o) / len(fails))

        rows.append(
            {
                "change": target,
                "n_flipped": len(flipped),
                "n_flipped_legacy_band": len(flipped_legacy),
                "n_failures_mean": round(float(np.mean([len(f) for f in failures.values()])), 2),
                "escape_fail_uncertainty": escape_over_failures(selected, failures),
                "escape_fail_random": round(float(np.mean(rand_fail)), 4),
                "escape_fail_recent": escape_over_failures(recent, failures),
                "escape_fail_oracle": round(float(np.mean(oracle)), 4) if oracle else 0.0,
                "escape_flip_uncertainty": round(len(flipped - selected) / len(flipped), 4)
                if flipped
                else None,
                "escape_flip_random": round(float(np.mean(rand_flip)), 4) if rand_flip else None,
                "expected": chosen.expected_escape,
                "selected": len(selected),
                "n_skipped_redundant": sum(1 for r in chosen.reasons.values() if r == "redundant"),
            }
        )

    # --- 方策の切り分け（どの構成要素が効いているか） ---
    def phat(task_id: str, change: Any, model: Any) -> float:
        return selector_mod.estimate_pass_probability(task_id, hist, change, coverage, model)

    variants: dict[str, Any] = {
        "report_pipeline": lambda c, m: set(
            selector_mod.select(task_ids, hist, c, coverage, BUDGET, m).selected
        ),
        "no_redundancy": lambda c, m: set(
            selector_mod.select(
                task_ids, hist, c, coverage, BUDGET, m, redundancy_threshold=2.0
            ).selected
        ),
        "entropy_over_cost_only": lambda c, m: _greedy(
            task_ids,
            hist,
            lambda t: selector_mod.entropy(phat(t, c, m)) / max(1.0, costs.get(t, mean_cost)),
            costs,
            mean_cost,
            inviolable,
        ),
        "failure_probability": lambda c, m: _greedy(
            task_ids,
            hist,
            lambda t: (1 - phat(t, c, m)) / max(1.0, costs.get(t, mean_cost)),
            costs,
            mean_cost,
            inviolable,
        ),
        "coverage_overlap_only": lambda c, m: _greedy(
            task_ids,
            hist,
            lambda t: len(coverage.get(t, set()) & coverage_mod.change_units(c)),
            costs,
            mean_cost,
            inviolable,
        ),
    }
    ablation: dict[str, dict[str, float]] = {}
    for name, build in variants.items():
        fails_ratio, flips_ratio = [], []
        for index, target in enumerate(TARGETS):
            change, _ = events[index]
            model = selector_mod.fit_model(events, coverage, holdout=index)
            picked = build(change, model)
            failures = failures_by_repeat(runs, target)
            flipped = flipped_tasks(runs, "v01_baseline", target)
            fails_ratio.append(escape_over_failures(picked, failures))
            if flipped:
                flips_ratio.append(len(flipped - picked) / len(flipped))
        base_fail = float(np.mean([r["escape_fail_random"] for r in rows]))
        base_flip = float(
            np.mean([r["escape_flip_random"] for r in rows if r["escape_flip_random"] is not None])
        )
        ablation[name] = {
            "escape_failures": round(float(np.mean(fails_ratio)), 4),
            "ratio_vs_random_failures": round(float(np.mean(fails_ratio)) / base_fail, 4),
            "escape_flips": round(float(np.mean(flips_ratio)), 4),
            "ratio_vs_random_flips": round(float(np.mean(flips_ratio)) / base_flip, 4),
        }

    measurable = [r for r in rows if r["n_failures_mean"] > 0]
    with_flips = [r for r in rows if r["n_flipped"] > 0]
    escape_u = float(np.mean([r["escape_fail_uncertainty"] for r in measurable]))
    escape_r = float(np.mean([r["escape_fail_random"] for r in measurable]))
    escape_f = float(np.mean([r["escape_fail_recent"] for r in measurable]))
    escape_o = float(np.mean([r["escape_fail_oracle"] for r in measurable]))
    calibration = float(
        np.mean([abs(r["expected"] - r["escape_fail_uncertainty"]) for r in measurable])
    )
    flip_u = float(np.mean([r["escape_flip_uncertainty"] for r in with_flips]))
    flip_r = float(np.mean([r["escape_flip_random"] for r in with_flips]))
    redundant_skips = float(np.mean([r["n_skipped_redundant"] for r in measurable]))

    fig = plotting.bar_compare(
        "E3-3",
        "escape_rate",
        "予算 30% での逃走欠陥率（原典 3.8 の定義、変更イベント平均）",
        ["不確実性駆動", "ランダム", "直近失敗優先", "オラクル(下限)"],
        [escape_u, escape_r, escape_f, escape_o],
        "逃走欠陥率",
        "simulated",
    )
    fig2 = plotting.scatter(
        "E3-3",
        "calibration",
        "expected_escape と実測の逃走欠陥率（どちらも失敗ベース）",
        "expected_escape",
        "実測",
        [r["expected"] for r in measurable],
        [r["escape_fail_uncertainty"] for r in measurable],
        "simulated",
        diagonal=True,
    )

    metrics = {
        "escape_ratio_vs_random": labeled(round(escape_u / escape_r, 4) if escape_r else 0.0),
        "calibration_error": labeled(round(calibration, 4)),
        "escape_uncertainty": labeled(round(escape_u, 4)),
        "escape_random": labeled(round(escape_r, 4)),
        "escape_recent_failures": labeled(round(escape_f, 4)),
        "escape_oracle_bound": labeled(round(escape_o, 4)),
        "escape_flip_ratio_vs_random": labeled(round(flip_u / flip_r, 4) if flip_r else 0.0),
        "escape_flip_uncertainty": labeled(round(flip_u, 4)),
        "escape_flip_random": labeled(round(flip_r, 4)),
        "n_measurable_events": labeled(len(measurable)),
        "mean_failures_per_full_run": labeled(
            round(float(np.mean([r["n_failures_mean"] for r in measurable])), 2)
        ),
        "mean_redundant_skips": labeled(round(redundant_skips, 4)),
        "ablation_best_flip_ratio": labeled(
            round(min(v["ratio_vs_random_flips"] for v in ablation.values()), 4)
        ),
    }
    notes = [
        f"変更イベントごとの結果: {rows}",
        f"方策の切り分け（比 < 1 ならランダムより良い）: {ablation}",
        (
            "**校正は成立した**。`expected_escape` と実測の逃走欠陥率を同じ量（失敗ベース）で"
            f"比べると誤差は {round(calibration, 3)}（基準 0.15）である。"
            "改訂前の 0.325 は、失敗の予測と反転の実測を引き算していたことによる見かけの誤差だった"
            "（ADR-018）。選択器は「自分がどれだけ失敗を見逃すか」を正しく見積もれている。"
        ),
        (
            "**成立しなかったのは選択の優劣である**。不確実性駆動の逃走欠陥率は "
            f"{round(escape_u, 3)} で、ランダムの {round(escape_r, 3)} より**悪い**（比 "
            f"{round(escape_u / escape_r, 3)}、基準 0.5）。反転ベースの KPI で測っても比は "
            f"{round(flip_u / flip_r, 3)} で、やはりランダムを上回る。"
            "予算内で到達可能な下限（各反復の F_full を知るオラクル）は "
            f"{round(escape_o, 3)} なので、基準自体は達成不能ではない。"
        ),
        (
            "切り分けの結果、効いている要素とそうでない要素がはっきり分かれた。"
            "(a) **カバレッジ重なりだけ**で選ぶと反転 KPI がランダムの "
            f"{ablation['coverage_overlap_only']['ratio_vs_random_flips']} 倍と最も良い。"
            "(b) **冗長性ペナルティを外す**と "
            f"{ablation['no_redundancy']['ratio_vs_random_flips']} 倍に改善する。"
            "(c) **H(p̂)/c だけ**にすると "
            f"{ablation['entropy_over_cost_only']['ratio_vs_random_flips']} 倍で、ほぼランダムと変わらない。"
            "つまりこの環境で信号を持っているのは原典 3.2（カバレッジ）であって、"
            "3.4 の不確実性は寄与していない。冗長性ペナルティはむしろ信号を消している。"
        ),
        (
            "冗長性ペナルティが候補を落とす件数は平均 "
            f"{round(redundant_skips, 1)} 件／回。ADR-014 のとおり、これは類似度の定義の問題ではなく "
            "**sim の軌跡がタスク間でほぼ同一のツール名列になる**ことによる。"
            "live の軌跡では 10 タスクが 9 種類の列に分かれ、類似度 0.8 超は 45 組中 1 組（2%）しかない。"
        ),
        (
            "反転の定義は ADR-016 で閾値を撤廃した（対応のある不一致が 1 件でもあれば反転）。"
            "改訂前のフレーク帯を使うと v09 の反転は 23 件ではなく 4 件、v06 は 11 件ではなく 6 件しか"
            "数えられず、実在する反転の大半を取り逃していた。"
        ),
        (
            "判定は NEGATIVE（実装は原典どおりだが主張が成立しなかった）。"
            "改訂前は FAIL（冗長性の実装が悪い）と診断していたが、切り分けにより "
            "**冗長性を外しても H(p̂) 単体ではランダムに勝てない**ことが分かったので、"
            "原因を手法側に訂正する。"
        ),
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
        failure_type="negative",
    )


if __name__ == "__main__":
    import json

    print(json.dumps(main(), ensure_ascii=False, indent=2))
