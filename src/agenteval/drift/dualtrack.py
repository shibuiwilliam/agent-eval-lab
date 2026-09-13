"""二重トラック（原典 8.6）。模擬とライブの合否一致 Cohen κ。"""

from __future__ import annotations

from dataclasses import dataclass

from sklearn.metrics import cohen_kappa_score

from agenteval.core.schema import Run


@dataclass
class KappaResult:
    """κ 1 件。"""

    kappa: float
    n: int
    agree: int
    mock_pass_rate: float
    live_pass_rate: float


def kappa(mock_runs: list[Run], live_runs: list[Run]) -> KappaResult:
    """同じタスク集合の模擬／ライブの合否から κ を出す。"""
    mock_by_task = {r.task_id: r.passed() for r in mock_runs}
    live_by_task = {r.task_id: r.passed() for r in live_runs}
    common = sorted(set(mock_by_task) & set(live_by_task))
    a = [int(mock_by_task[t]) for t in common]
    b = [int(live_by_task[t]) for t in common]
    if not common:
        return KappaResult(kappa=0.0, n=0, agree=0, mock_pass_rate=0.0, live_pass_rate=0.0)
    if len(set(a)) == 1 and len(set(b)) == 1:
        # 分散が無いと κ は未定義。完全一致なら 1.0、不一致なら 0.0 とする
        value = 1.0 if a == b else 0.0
    else:
        value = float(cohen_kappa_score(a, b))
    return KappaResult(
        kappa=round(value, 4),
        n=len(common),
        agree=sum(1 for x, y in zip(a, b, strict=True) if x == y),
        mock_pass_rate=round(sum(a) / len(a), 4),
        live_pass_rate=round(sum(b) / len(b), 4),
    )
