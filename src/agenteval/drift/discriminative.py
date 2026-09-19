"""弁別力と飽和（原典 8.4）。"""

from __future__ import annotations

from dataclasses import dataclass

from agenteval.core.registry import get_version
from agenteval.core.schema import Run


@dataclass
class Discriminative:
    """テスト 1 件の弁別力。"""

    task_id: str
    good_rate: float
    bad_rate: float
    d: float
    saturated: bool
    n_good: int
    n_bad: int


def discriminative_power(runs: list[Run]) -> float:
    """`d(t)` = good 版の合格率 − bad 版の合格率。neutral は除外する。"""
    good = [r for r in runs if get_version(r.version_id).quality_label == "good"]
    bad = [r for r in runs if get_version(r.version_id).quality_label == "bad"]
    if not good or not bad:
        return 0.0
    good_rate = sum(r.passed() for r in good) / len(good)
    bad_rate = sum(r.passed() for r in bad) / len(bad)
    return round(good_rate - bad_rate, 4)


def evaluate(task_id: str, runs: list[Run]) -> Discriminative:
    """1 タスクの弁別力と飽和判定。"""
    subset = [r for r in runs if r.task_id == task_id]
    good = [r for r in subset if get_version(r.version_id).quality_label == "good"]
    bad = [r for r in subset if get_version(r.version_id).quality_label == "bad"]
    good_rate = sum(r.passed() for r in good) / len(good) if good else 0.0
    bad_rate = sum(r.passed() for r in bad) / len(bad) if bad else 0.0
    d = round(good_rate - bad_rate, 4)
    # `.claude/rules/drift.md`: 飽和 = **全版で**合格率 > 0.95 かつ |d| < 0.1。
    # 改訂前は全 run をプールした合格率を見ており、一部の版だけ低くても飽和と判定しえた。
    by_version: dict[str, list[bool]] = {}
    for r in subset:
        by_version.setdefault(r.version_id, []).append(r.passed())
    min_rate = min((sum(v) / len(v) for v in by_version.values()), default=0.0)
    saturated = min_rate > 0.95 and abs(d) < 0.1
    return Discriminative(
        task_id=task_id,
        good_rate=round(good_rate, 4),
        bad_rate=round(bad_rate, 4),
        d=d,
        saturated=saturated,
        n_good=len(good),
        n_bad=len(bad),
    )


def evaluate_all(runs: list[Run]) -> dict[str, Discriminative]:
    """全タスクの弁別力。"""
    task_ids = sorted({r.task_id for r in runs})
    return {task_id: evaluate(task_id, runs) for task_id in task_ids}
