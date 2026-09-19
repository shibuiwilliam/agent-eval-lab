"""E3-7 逃走欠陥率・ε-探索・不可侵集合（原典 3.8）。"""

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
    flipped_tasks,
    labeled,
)

from agenteval.core.registry import get_task, load_tasks
from agenteval.pts import change as change_mod
from agenteval.pts import coverage as coverage_mod
from agenteval.pts import kpi as kpi_mod
from agenteval.pts import selector as selector_mod
from agenteval.reports import plotting

TARGETS = ["v02_dateformat", "v05_unsafe_delete", "v06_toolschema_v2", "v07_step_minimizer"]
BUDGETS = [0.05, 0.2, 0.4, 1.0]
EPSILON = 0.05

METHOD = """予算比を 5% / 20% / 40% / 100% と変えて選択し、逃走欠陥率・ε-探索で混ざったランダムテストの
件数・不可侵集合（`risk: inviolable`）の選択率を測った（原典 3.8）。

逃走欠陥率は原典 3.8 の定義どおり `Escape(S) = |F_full \\ S| / |F_full|` で、`F_full` は
**そのフル実行で不合格になったタスク**である（ADR-018）。フル実行 1 回 = 各テスト 1 回なので、
反復ごとに `F_full(r)` を作って平均した。反転ベースの値（改訂前の定義）は診断として併記する。

不可侵集合は選択ロジックの外で必ず選ばれる実装なので、予算 5% でも 100% であることを確認する。
予算内で到達可能な下限は、各反復の `F_full` を事前に知っているオラクルで出した。
不確実性の推定に使う履歴は現行配備 v01_baseline に限る（ADR-022）。
対照は予算 100%（全件実行 → 逃走欠陥率 0）。"""


def main(live: bool = False, seed: int = 20260913) -> dict[str, Any]:
    runs = corpus()
    task_ids = sorted(load_tasks())
    history = [r for r in runs if r.version_id == "v01_baseline"]
    coverage = coverage_mod.coverage_map(history)
    costs = selector_mod.cost_of(history)
    mean_cost = float(np.mean(list(costs.values()))) if costs else 1.0
    inviolable = [t for t in task_ids if get_task(t).risk == "inviolable"]

    def oracle_bound(failures: dict[int, set[str]], budget_ratio: float) -> float:
        """各反復の F_full を事前に知っているときの逃走欠陥率（予算内の下限）。"""
        budget_tokens = budget_ratio * sum(costs.get(t, mean_cost) for t in task_ids)
        rates = []
        for fails in failures.values():
            if not fails:
                continue
            picked = set(inviolable)
            spent = sum(costs.get(t, mean_cost) for t in inviolable)
            for task_id in sorted(fails, key=lambda t: costs.get(t, mean_cost)):
                cost = costs.get(task_id, mean_cost)
                if task_id in picked or spent + cost > budget_tokens:
                    continue
                picked.add(task_id)
                spent += cost
            rates.append(len(fails - picked) / len(fails))
        return round(float(np.mean(rates)), 4) if rates else 0.0

    rows = []
    for target in TARGETS:
        merged = change_mod.merge(change_mod.diff_versions("v01_baseline", target))
        assert merged is not None
        flipped = flipped_tasks(runs, "v01_baseline", target)
        failures = failures_by_repeat(runs, target)
        for budget in BUDGETS:
            selection = selector_mod.select(
                task_ids, history, merged, coverage, budget_ratio=budget, epsilon=EPSILON, seed=seed
            )
            result = kpi_mod.evaluate(selection, set(), task_ids)
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
                    "n_failures_mean": round(
                        float(np.mean([len(f) for f in failures.values()])), 2
                    ),
                    "escape": escape_over_failures(set(selection.selected), failures),
                    "escape_flips": kpi_mod.escape_rate_flips(selection, flipped),
                    "escape_oracle": oracle_bound(failures, budget),
                    "random_included": result.random_included,
                    "inviolable_rate": result.inviolable_rate,
                    "selected": len(selection.selected),
                    "escape_no_redundancy": escape_over_failures(set(variant.selected), failures),
                }
            )

    measurable = [r for r in rows if r["n_failures_mean"] > 0]
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
        "escape_oracle_at40": labeled(
            round(float(np.mean([r["escape_oracle"] for r in at40])), 4) if at40 else 0.0
        ),
        "escape_flips_at40": labeled(
            round(
                float(np.mean([r["escape_flips"] for r in at40 if r["n_flipped"] > 0])),
                4,
            )
            if [r for r in at40 if r["n_flipped"] > 0]
            else 0.0
        ),
        "mean_failures_per_full_run": labeled(
            round(float(np.mean([r["n_failures_mean"] for r in at40])), 2) if at40 else 0.0
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
            "逃走欠陥率 ≤ 0.1 は満たせなかった（予算 40% で 0.479）。"
            "ただし**基準が達成不能なわけではない**。各反復の `F_full` を事前に知っているオラクルは "
            "同じ予算で 0.061 まで下げられるので、予算 40% は失敗を覆うのに十分である。"
            "届かないのは選択の側である。"
        ),
        (
            "E3-3 の切り分けと整合する。冗長性ペナルティを外すと 0.383 まで下がるが、それでも 0.1 には遠い。"
            "E3-3 では、この環境で信号を持っているのはカバレッジ重なり（原典 3.2）であって "
            "H(p̂) の不確実性（3.4）ではないことを、方策を 5 通り並べて確かめている。"
        ),
        (
            "**原因の訂正（live で確認、ADR-014）**: 「同種タスクが相互に重複と判定される」現象は、"
            "sim の軌跡がタスク間でほぼ同一のツール名列になるために起きている。"
            "live の軌跡では 10 タスクが 9 種類の列に分かれ、類似度 0.8 を超える組は 45 組中 1 組（2%）しかない。"
            "冗長性ペナルティの定義そのものは変更しない（E3-3 の訂正と同じ）。"
        ),
        (
            "反転ベースの逃走欠陥率（改訂前の定義）は予算 40% で 0.318。"
            "反転の判定は ADR-016 で閾値を撤廃している。"
        ),
        (
            "判定は NEGATIVE。**手法の骨格は成立している**: 不可侵集合は予算 5% でも 100% 選択され、"
            "ε-探索は毎回 2 件を混ぜ、予算 100% では逃走欠陥率 0 になる。"
            "成立しなかったのは「予算 40% で逃走欠陥率 ≤ 0.1」という選択性能の主張だけである。"
            "改訂前は FAIL（冗長性の実装が悪い）と診断していたが、E3-3 の切り分けで "
            "冗長性を外しても届かないことが分かったので、原因を手法側に訂正する。"
        ),
    ]
    return finalize(
        "E3-7",
        metrics,
        METHOD,
        notes,
        figures=[("逃走欠陥率の曲線", fig)],
        seed=seed,
        failure_type="negative",
    )


if __name__ == "__main__":
    import json

    print(json.dumps(main(), ensure_ascii=False, indent=2))
