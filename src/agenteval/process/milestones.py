"""半順序マイルストーンの到達判定と部分点（原典 4.5）。

「どの順序で到達したか」を見るため、ステップごとの状態列を入力に取る。
順序制約（`after`）に違反して到達したマイルストーンは部分点に数えない。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agenteval.core.registry import Milestone, Task
from agenteval.core.schema import Run
from agenteval.env.checks import CheckContext, run_check


@dataclass
class MilestoneResult:
    """マイルストーン判定の結果。"""

    score: float
    first_step: dict[str, int]
    ordered_ok: dict[str, bool]

    def achieved(self) -> list[str]:
        return [k for k, v in self.first_step.items() if v >= 0]


def evaluate(
    task: Task,
    run: Run,
    states: list[dict[str, list[dict[str, Any]]]],
    initial_state_hash: str = "",
) -> MilestoneResult:
    """ステップ i 終了時点の状態列 `states` に対してマイルストーンを判定する。

    `states[i]` はステップ i 実行後の環境 dump。長さは run.steps と同じであること。
    """
    first_step: dict[str, int] = {}
    for milestone in task.milestones:
        first_step[milestone.id] = -1
        for i, state in enumerate(states):
            prefix = Run(
                run_id=run.run_id,
                task_id=run.task_id,
                version_id=run.version_id,
                version_hash=run.version_hash,
                mode=run.mode,
                seed=run.seed,
                steps=run.steps[: i + 1],
                final_text=run.final_text if i == len(states) - 1 else "",
                claims=run.claims if i == len(states) - 1 else [],
            )
            ctx = CheckContext(run=prefix, state=state, initial_state_hash=initial_state_hash)
            if run_check(ctx, milestone.check):
                first_step[milestone.id] = i
                break
    ordered_ok = _order_ok(task.milestones, first_step)
    total = len(task.milestones)
    score = (sum(1 for m in task.milestones if ordered_ok[m.id]) / total) if total else 0.0
    return MilestoneResult(score=score, first_step=first_step, ordered_ok=ordered_ok)


def _order_ok(milestones: list[Milestone], first_step: dict[str, int]) -> dict[str, bool]:
    """`after` の制約を満たして到達したか。未到達は False。"""
    out: dict[str, bool] = {}
    for milestone in milestones:
        step = first_step.get(milestone.id, -1)
        if step < 0:
            out[milestone.id] = False
            continue
        ok = True
        for dep in milestone.after:
            dep_step = first_step.get(dep, -1)
            if dep_step < 0 or dep_step > step:
                ok = False
                break
        out[milestone.id] = ok
    return out
