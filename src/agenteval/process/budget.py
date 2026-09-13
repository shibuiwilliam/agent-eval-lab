"""コスト予算と Goodhart ペア指標（原典 4.11）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from agenteval.core.registry import get_task
from agenteval.core.schema import Run
from agenteval.process.metrics import RunMetrics, aggregate

# Goodhart のペア（必ず同じ表に並べる）
GOODHART_PAIRS: list[tuple[str, str]] = [
    ("steps", "verification_rate"),
    ("tokens", "recall"),
    ("questions", "wrong_premise_rate"),
    ("wall_time_s", "scope_violations"),
]


@dataclass
class BudgetResult:
    """予算判定 1 件。"""

    run_id: str
    category: str
    tokens: int
    p95: float
    exceeded: bool


def p95_by_category(runs: list[Run], factor: float = 1.5) -> dict[str, float]:
    """ベースライン版の種別ごとの p95 × factor を予算の初期値にする。"""
    buckets: dict[str, list[int]] = {}
    for run in runs:
        category = get_task(run.task_id).category
        buckets.setdefault(category, []).append(run.cost.total_tokens)
    return {
        category: float(np.percentile(values, 95) * factor)
        for category, values in sorted(buckets.items())
        if values
    }


def check_budgets(runs: list[Run], budgets: dict[str, float]) -> list[BudgetResult]:
    """各 run が種別予算を超えたか。"""
    out = []
    for run in runs:
        category = get_task(run.task_id).category
        p95 = budgets.get(category, float("inf"))
        out.append(
            BudgetResult(
                run_id=run.run_id,
                category=category,
                tokens=run.cost.total_tokens,
                p95=p95,
                exceeded=run.cost.total_tokens > p95,
            )
        )
    return out


def goodhart_table(metrics: list[RunMetrics]) -> dict[str, dict[str, float]]:
    """効率指標とペアの品質指標を版ごとに並べた表。"""
    versions = sorted({m.version_id for m in metrics})
    table: dict[str, dict[str, float]] = {}
    for version in versions:
        subset = [m for m in metrics if m.version_id == version]
        table[version] = {
            "steps": aggregate(subset, "steps"),
            "verification_rate": aggregate(subset, "verification_rate"),
            "tokens": aggregate(subset, "tokens"),
            "milestone_score": aggregate(subset, "milestone_score"),
            "violations": aggregate(subset, "violations"),
            "pass_rate": aggregate(subset, "passed"),
        }
    return table


def exceed_rate(results: list[BudgetResult]) -> dict[str, Any]:
    """超過率の集計。"""
    if not results:
        return {"rate": 0.0, "n": 0}
    exceeded = [r for r in results if r.exceeded]
    return {
        "rate": round(len(exceeded) / len(results), 4),
        "n": len(results),
        "exceeded_runs": [r.run_id for r in exceeded][:10],
    }
