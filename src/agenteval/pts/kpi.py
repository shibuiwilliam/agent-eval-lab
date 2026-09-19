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


def escape_rate(selection: Selection, failures: set[str]) -> float:
    """逃走欠陥率 `Escape(S) = |F_full \\ S| / |F_full|`（原典 3.8）。

    `F_full` は**フル実行で見つかった失敗**の集合である（ADR-018）。
    改訂前はここに「反転したテスト」を渡していたが、`selector.expected_escape` が
    失敗質量 `Σ(1 − p̂)` を予測している以上、校正誤差を取るには両者が同じ量でなければならない。
    反転ベースの値が要るときは `escape_rate_flips()` を使う。
    """
    if not failures:
        return 0.0
    missed = [t for t in failures if t not in selection.selected]
    return round(len(missed) / len(failures), 4)


def escape_rate_flips(selection: Selection, flipped: set[str]) -> float:
    """診断用: 反転したテストのうち選ばなかった割合（改訂前の定義）。"""
    if not flipped:
        return 0.0
    missed = [t for t in flipped if t not in selection.selected]
    return round(len(missed) / len(flipped), 4)


def evaluate(selection: Selection, failures: set[str], all_tasks: list[str]) -> KPIResult:
    """KPI をまとめて出す。`failures` はフル実行で見つかった失敗（原典 3.8）。"""
    inviolable = [t for t in all_tasks if get_task(t).risk == "inviolable"]
    covered = [t for t in inviolable if t in selection.selected]
    return KPIResult(
        escape_rate=escape_rate(selection, failures),
        n_flipped=len(failures),
        n_missed=len([t for t in failures if t not in selection.selected]),
        random_included=sum(1 for r in selection.reasons.values() if r == "epsilon"),
        inviolable_rate=round(len(covered) / len(inviolable), 4) if inviolable else 1.0,
    )
