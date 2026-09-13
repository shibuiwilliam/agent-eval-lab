"""trace から決定的に導くイベント列（リンターの入力）。

イベント種別は call / result / plan / claim_done / finish の 5 つだけ（規則どおり）。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from agenteval.core.schema import Run

EventKind = Literal["call", "result", "plan", "claim_done", "finish"]


class Event(BaseModel):
    """リンターが見るイベント 1 件。"""

    kind: EventKind
    step: int
    tool: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    is_error: bool = False
    content: str = ""
    call_id: str | None = None


def to_events(run: Run) -> list[Event]:
    """Run → イベント列。同じ Run からは常に同じ列が出る。"""
    events: list[Event] = []
    for step in run.steps:
        results = {r.id: r for r in step.tool_results}
        for call in step.tool_calls:
            if call.name == "submit_plan":
                events.append(Event(kind="plan", step=step.i, tool=call.name, args=call.args_norm))
            events.append(
                Event(
                    kind="call",
                    step=step.i,
                    tool=call.name,
                    args=call.args_norm,
                    call_id=call.id,
                )
            )
            result = results.get(call.id)
            if result is not None:
                events.append(
                    Event(
                        kind="result",
                        step=step.i,
                        tool=call.name,
                        is_error=result.is_error,
                        content=result.content,
                        call_id=call.id,
                    )
                )
            if call.name == "finish":
                for claim in call.args.get("claims", []) or []:
                    events.append(
                        Event(kind="claim_done", step=step.i, tool=call.name, content=str(claim))
                    )
                events.append(Event(kind="finish", step=step.i, tool=call.name))
    return events
