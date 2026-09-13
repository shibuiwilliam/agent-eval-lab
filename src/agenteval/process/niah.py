"""軌跡内 NIAH（原典 4.9）。ノイズ量に対する想起率 `R(n)` と AUC。"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise

import numpy as np

from agenteval.core.registry import Task
from agenteval.core.schema import Run


@dataclass
class RecallPoint:
    """ノイズ量 n における想起率。"""

    n: int
    recall: float
    runs: int


def recalled(task: Task, run: Run) -> bool:
    """最終テキストに key_fact が含まれるか。"""
    if not task.key_fact:
        return False
    return task.key_fact in run.final_text


def recall_curve(task: Task, runs_by_noise: dict[int, list[Run]]) -> list[RecallPoint]:
    """`R(n)` の曲線。"""
    points = []
    for n in sorted(runs_by_noise):
        runs = runs_by_noise[n]
        hits = sum(1 for r in runs if recalled(task, r))
        points.append(
            RecallPoint(n=n, recall=round(hits / len(runs), 4) if runs else 0.0, runs=len(runs))
        )
    return points


def auc(points: list[RecallPoint]) -> float:
    """曲線下面積（n を 0〜1 に正規化した台形則）。"""
    if len(points) < 2:
        return points[0].recall if points else 0.0
    xs = np.array([p.n for p in points], dtype=float)
    ys = np.array([p.recall for p in points], dtype=float)
    xs = (xs - xs.min()) / (xs.max() - xs.min()) if xs.max() > xs.min() else xs
    return float(round(np.trapezoid(ys, xs), 4))


def is_monotonic(points: list[RecallPoint], tolerance: float = 0.05) -> bool:
    """単調減少か（許容幅つき）。単調でなければ注入量を見直す信号にする。"""
    values = [p.recall for p in points]
    return all(b <= a + tolerance for a, b in pairwise(values))
