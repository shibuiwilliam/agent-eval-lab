"""半順序マイルストーンの単体テスト（合成軌跡）。"""

from __future__ import annotations

from typing import Any

from agenteval.core.registry import get_task
from agenteval.core.schema import Run, Step, ToolCall
from agenteval.process.milestones import evaluate


def _run(tools_per_step: list[list[str]]) -> Run:
    steps = []
    for i, names in enumerate(tools_per_step):
        steps.append(
            Step(
                i=i,
                state_hash_before="a",
                state_hash_after="a",
                tool_calls=[
                    ToolCall(id=f"t{i}{j}", name=n, args={}, args_norm={})
                    for j, n in enumerate(names)
                ],
            )
        )
    return Run(
        run_id="r",
        task_id="T-001",
        version_id="v",
        version_hash="h",
        mode="sim",
        seed=0,
        steps=steps,
    )


def _state(with_event: bool) -> dict[str, list[dict[str, Any]]]:
    events = (
        [
            {
                "id": "e9",
                "title": "田中さんと打合せ",
                "start": "2026-09-21T11:00",
                "end": "2026-09-21T11:30",
                "room": "A",
                "attendees": "c001",
            }
        ]
        if with_event
        else []
    )
    return {"contacts": [], "events": events, "mails": [], "files": [], "backups": [], "notes": []}


def test_in_order_gets_full_score() -> None:
    task = get_task("T-001")
    run = _run([["calendar_search"], ["calendar_create"], ["checks_run"]])
    states = [_state(False), _state(True), _state(True)]
    result = evaluate(task, run, states)
    assert result.score == 1.0
    assert result.first_step == {"M1": 0, "M2": 1, "M3": 2}


def test_out_of_order_loses_points() -> None:
    """検査を先にやって、後から予定を作る順序違い。M3 は after=[M2] を満たさない。"""
    task = get_task("T-001")
    run = _run([["calendar_search"], ["checks_run"], ["calendar_create"]])
    states = [_state(False), _state(False), _state(True)]
    result = evaluate(task, run, states)
    assert result.ordered_ok["M3"] is False
    assert result.score < 1.0


def test_unreached_milestone_scores_zero() -> None:
    task = get_task("T-001")
    run = _run([["calendar_search"]])
    result = evaluate(task, run, [_state(False)])
    assert result.score == 1 / 3
