"""選択の KPI（原典 3.8）: 逃走欠陥率・ε-探索・不可侵集合。"""

from __future__ import annotations

from dataclasses import dataclass

from agenteval.core.registry import get_task
from agenteval.pts.selector import Selection


@dataclass
class KPIResult:
    """KPI 1 件。"""

    escape_rate: float
    n_flipped: int
    n_missed: int
    random_included: int
    inviolable_rate: float


def escape_rate(selection: Selection, flipped: set[str]) -> float:
    """逃走欠陥率 = 選ばなかったテストのうち実際に反転していたものの割合。

    分母は反転したテストの総数（反転が無ければ 0）。
    """
    if not flipped:
        return 0.0
    missed = [t for t in flipped if t not in selection.selected]
    return round(len(missed) / len(flipped), 4)


def evaluate(selection: Selection, flipped: set[str], all_tasks: list[str]) -> KPIResult:
    """KPI をまとめて出す。"""
    inviolable = [t for t in all_tasks if get_task(t).risk == "inviolable"]
    covered = [t for t in inviolable if t in selection.selected]
    return KPIResult(
        escape_rate=escape_rate(selection, flipped),
        n_flipped=len(flipped),
        n_missed=len([t for t in flipped if t not in selection.selected]),
        random_included=sum(1 for r in selection.reasons.values() if r == "epsilon"),
        inviolable_rate=round(len(covered) / len(inviolable), 4) if inviolable else 1.0,
    )
