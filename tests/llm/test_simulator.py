"""シミュレータの単体テスト（ADR-008）。"""

from __future__ import annotations

from agenteval.core.registry import get_task
from agenteval.llm.client import MessageRequest
from agenteval.llm.simulator import Luck, SimFlags, Simulator


def test_flags_come_from_prompt_sections() -> None:
    prompt = (
        "<!-- section: workflow -->\n検索してから書く\n<!-- section: verification -->\n検査する\n"
    )
    flags = SimFlags.from_prompt(prompt)
    assert flags.search_before_write and flags.verify_before_finish
    assert not flags.backup_before_delete


def test_luck_is_stable_across_versions() -> None:
    a = Luck.draw("T-001", 1, 0)
    b = Luck.draw("T-001", 1, 0)
    c = Luck.draw("T-001", 1, 1)
    assert a == b
    assert a != c


def test_simulator_first_action_is_search() -> None:
    task = get_task("T-001")
    sim = Simulator(task=task, seed=1)
    req = MessageRequest(
        model="claude-haiku-4-5-20251001",
        system="<!-- section: workflow -->\n検索してから書く\n",
        messages=[{"role": "user", "content": [{"type": "text", "text": task.prompt}]}],
    )
    result = sim.respond(req)
    assert result.tool_uses()[0]["name"] == "calendar_search"


def test_forced_plan_tool_choice() -> None:
    task = get_task("T-001")
    sim = Simulator(task=task, seed=1)
    req = MessageRequest(
        model="claude-haiku-4-5-20251001",
        system="x",
        messages=[{"role": "user", "content": [{"type": "text", "text": task.prompt}]}],
        tool_choice={"type": "tool", "name": "submit_plan"},
    )
    result = sim.respond(req)
    assert result.tool_uses()[0]["name"] == "submit_plan"
