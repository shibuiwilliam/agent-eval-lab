"""イベント列導出の単体テスト。"""

from __future__ import annotations

from agenteval.core.events import to_events
from agenteval.core.schema import Run, Step, ToolCall, ToolResult


def _run() -> Run:
    step = Step(
        i=0,
        state_hash_before="a",
        state_hash_after="b",
        tool_calls=[
            ToolCall(id="t1", name="file_read", args={"path": "x"}, args_norm={"path": "x"}),
            ToolCall(
                id="t2",
                name="finish",
                args={"summary": "done", "claims": ["c1", "c2"]},
                args_norm={},
            ),
        ],
        tool_results=[
            ToolResult(id="t1", content="{}", is_error=False),
            ToolResult(id="t2", content="{}", is_error=False),
        ],
    )
    return Run(
        run_id="r",
        task_id="T",
        version_id="v",
        version_hash="h",
        mode="sim",
        seed=0,
        steps=[step],
    )


def test_event_kinds() -> None:
    kinds = [e.kind for e in to_events(_run())]
    assert kinds == ["call", "result", "call", "result", "claim_done", "claim_done", "finish"]


def test_events_are_deterministic() -> None:
    assert [e.model_dump() for e in to_events(_run())] == [
        e.model_dump() for e in to_events(_run())
    ]
