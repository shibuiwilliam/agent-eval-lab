"""pass@k / pass^k・ステップ数分散・行動エントロピー（原典 4.10）。"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

from agenteval.core.normalize import action_key
from agenteval.core.schema import Run


@dataclass
class ConsistencyResult:
    """タスク 1 件の一貫性指標。"""

    task_id: str
    n: int
    pass_rate: float
    pass_at_k: dict[int, float]
    pass_pow_k: dict[int, float]
    step_variance: float
    action_entropy: float


def pass_at_k(n: int, c: int, k: int) -> float:
    """原典 4.10 の pass@k。n 回中 c 回成功したときに k 回引いて 1 回でも成功する確率。"""
    if k > n:
        raise ValueError("k は n 以下であること")
    if n - c < k:
        return 1.0
    return 1.0 - math.comb(n - c, k) / math.comb(n, k)


def pass_pow_k(n: int, c: int, k: int) -> float:
    """pass^k。k 回引いて全部成功する確率。"""
    if k > n:
        raise ValueError("k は n 以下であること")
    if c < k:
        return 0.0
    return math.comb(c, k) / math.comb(n, k)


def action_entropy(runs: list[Run]) -> float:
    """同一タスクの反復での行動列のばらつき（同値類の分布のエントロピー、bit）。"""
    if not runs:
        return 0.0
    signatures = [
        "|".join(action_key(c.name, c.args_norm) for s in r.steps for c in s.tool_calls)
        for r in runs
    ]
    counts = Counter(signatures)
    total = sum(counts.values())
    return -sum((v / total) * math.log2(v / total) for v in counts.values())


def evaluate(task_id: str, runs: list[Run], ks: tuple[int, ...] = (1, 2, 3)) -> ConsistencyResult:
    """1 タスクの反復群から一貫性指標を出す。"""
    n = len(runs)
    c = sum(1 for r in runs if r.passed())
    steps = [r.n_steps for r in runs]
    mean = sum(steps) / n if n else 0.0
    variance = sum((s - mean) ** 2 for s in steps) / n if n else 0.0
    return ConsistencyResult(
        task_id=task_id,
        n=n,
        pass_rate=c / n if n else 0.0,
        pass_at_k={k: pass_at_k(n, c, k) for k in ks if k <= n},
        pass_pow_k={k: pass_pow_k(n, c, k) for k in ks if k <= n},
        step_variance=round(variance, 4),
        action_entropy=round(action_entropy(runs), 4),
    )


def flake_rate(runs: list[Run]) -> float:
    """フレーク率 = 同一 (task, version) の反復で合否が割れた割合。"""
    groups: dict[tuple[str, str], list[bool]] = {}
    for run in runs:
        groups.setdefault((run.task_id, run.version_id), []).append(run.passed())
    flaky = sum(1 for outcomes in groups.values() if 0 < sum(outcomes) < len(outcomes))
    return round(flaky / len(groups), 4) if groups else 0.0


def flake_band(runs: list[Run], version_id: str) -> float:
    """フレーク帯 = ベースライン版の反復で合否が割れたタスクの割合。

    この帯の内側の変化は「変化」と数えない（02-experiment-catalog.md の定義）。
    """
    groups: dict[str, list[bool]] = {}
    for run in runs:
        if run.version_id == version_id:
            groups.setdefault(run.task_id, []).append(run.passed())
    if not groups:
        return 0.0
    split = [1.0 if 0 < sum(v) < len(v) else 0.0 for v in groups.values()]
    return round(sum(split) / len(split), 4)
