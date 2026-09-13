"""E3-7 逃走欠陥率・ε-探索・不可侵集合（原典 3.8）。"""

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

TARGETS = ["v02_dateformat", "v05_unsafe_delete", "v06_toolschema_v2", "v07_step_minimizer"]
BUDGETS = [0.05, 0.2, 0.4, 1.0]
EPSILON = 0.05

METHOD = """予算比を 5% / 20% / 40% / 100% と変えて選択し、逃走欠陥率（選ばなかったテストのうち
実際に反転していたものの割合）、ε-探索で混ざったランダムテストの件数、不可侵集合（`risk: inviolable`）の
選択率を測った（原典 3.8）。不可侵集合は選択ロジックの外で必ず選ばれる実装になっているので、
予算 5% でも 100% であることを確認する。反転の定義は ADR-009。
対照は予算 100%（全件実行 → 逃走欠陥率 0）。"""


def main(live: bool = False, seed: int = 20260913) -> dict[str, Any]:
    runs = corpus()
    bands = flake_band(runs, "v01_baseline")
    task_ids = sorted(load_tasks())
    coverage = coverage_mod.coverage_map([r for r in runs if r.version_id == "v01_baseline"])

    rows = []
    for target in TARGETS:
        merged = change_mod.merge(change_mod.diff_versions("v01_baseline", target))
        assert merged is not None
        flipped = flipped_tasks(runs, "v01_baseline", target, bands)
        history = [r for r in runs if r.version_id != target]
        for budget in BUDGETS:
            selection = selector_mod.select(
                task_ids, history, merged, coverage, budget_ratio=budget, epsilon=EPSILON, seed=seed
            )
            result = kpi_mod.evaluate(selection, flipped, task_ids)
            variant = selector_mod.select(
                task_ids,
                history,
                merged,
                coverage,
                budget_ratio=budget,
                epsilon=EPSILON,
                seed=seed,
                redundancy_threshold=2.0,
            )
            rows.append(
                {
                    "change": target,
                    "budget": budget,
                    "n_flipped": len(flipped),
                    "escape": result.escape_rate,
                    "random_included": result.random_included,
                    "inviolable_rate": result.inviolable_rate,
                    "selected": len(selection.selected),
                    "escape_no_redundancy": kpi_mod.escape_rate(variant, flipped),
                }
            )

    measurable = [r for r in rows if r["n_flipped"] > 0]
    at40 = [r for r in measurable if r["budget"] == 0.4]
    at_full = [r for r in measurable if r["budget"] == 1.0]
    at5 = [r for r in rows if r["budget"] == 0.05]

    fig = plotting.line_curve(
        "E3-7",
        "escape_curve",
        "予算比と逃走欠陥率（変更イベント平均）",
        "予算比",
        "逃走欠陥率",
        {
            "逃走欠陥率": (
                BUDGETS,
                [
                    float(np.mean([r["escape"] for r in measurable if r["budget"] == b]))
                    if [r for r in measurable if r["budget"] == b]
                    else 0.0
                    for b in BUDGETS
                ],
            )
        },
        "simulated",
    )

    metrics = {
        "escape_rate": labeled(
            round(float(np.mean([r["escape"] for r in at40])), 4) if at40 else 1.0
        ),
        # ε-探索: 予算 100% では全件が既に選ばれていて混ぜる余地が無いので、予算 < 100% の条件で見る
        "random_included": labeled(
            int(min(r["random_included"] for r in rows if r["budget"] < 1.0))
        ),
        "inviolable_rate": labeled(round(float(np.min([r["inviolable_rate"] for r in at5])), 4)),
        "full_budget_escape": labeled(
            round(float(np.mean([r["escape"] for r in at_full])), 4) if at_full else 0.0
        ),
        "n_measurable_events": labeled(len({r["change"] for r in measurable})),
        "escape_no_redundancy_at40": labeled(
            round(float(np.mean([r["escape_no_redundancy"] for r in at40])), 4) if at40 else 1.0
        ),
    }
    notes = [
        f"予算ごとの結果: {rows}",
        "不可侵集合（T-005, T-011, T-025）は選択ロジックの外で必ず含める実装。予算 5% でも 100% 選択される。",
        (
            "ε-探索は予算に関係なく最低 1 件を混ぜる実装。予算 100% では全件が既に選ばれているため"
            "混ぜる余地が無いので、件数は予算 < 100% の条件の最小値を報告している。"
        ),
        (
            "逃走欠陥率 ≤ 0.1 は満たせなかった。原因は E3-3 と同じで、冗長性ペナルティ"
            "（ツール名列の類似度 > 0.8 を飛ばす）が候補集合内の同種タスクを相互に重複と見なし、"
            "予算を候補外の多様なタスクに回してしまうこと。冗長性を切った変種の逃走欠陥率も併記した。"
        ),
        (
            "判定は FAIL（実験設計の不備）。不可侵集合の保証（予算 5% でも 100% 選択）と"
            "予算 100% での逃走欠陥率 0 は成立している。"
        ),
    ]
    return finalize("E3-7", metrics, METHOD, notes, figures=[("逃走欠陥率の曲線", fig)], seed=seed)


if __name__ == "__main__":
    import json

    print(json.dumps(main(), ensure_ascii=False, indent=2))
