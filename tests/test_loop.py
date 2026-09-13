"""エージェントループの単体テスト（sim モード。API を呼ばない）。"""

from __future__ import annotations

import pytest

from agenteval.agent.loop import RunOptions, run_task
from agenteval.core.registry import get_task, get_version
from agenteval.core.store import trace_hash


@pytest.mark.parametrize("task_id", ["T-001", "T-002", "T-003", "T-004", "T-005"])
def test_run_is_deterministic(task_id: str) -> None:
    task, version = get_task(task_id), get_version("v01_baseline")
    a = run_task(task, version, RunOptions(mode="sim", seed=7))
    b = run_task(task, version, RunOptions(mode="sim", seed=7))
    assert trace_hash(a) == trace_hash(b)


def test_run_terminates_with_finish() -> None:
    run = run_task(get_task("T-001"), get_version("v01_baseline"), RunOptions(mode="sim", seed=7))
    assert run.tool_names()[-1] == "finish"
    assert run.outcome is not None
    assert run.claims


def test_state_hash_changes_only_on_writes() -> None:
    run = run_task(get_task("T-001"), get_version("v01_baseline"), RunOptions(mode="sim", seed=7))
    changed = [s.i for s in run.steps if s.state_hash_before != s.state_hash_after]
    writes = [s.i for s in run.steps if any(c.name == "calendar_create" for c in s.tool_calls)]
    assert changed == writes


def test_acceptance_is_evaluated() -> None:
    run = run_task(get_task("T-005"), get_version("v01_baseline"), RunOptions(mode="sim", seed=7))
    assert run.outcome is not None
    assert set(run.outcome.acceptance_results) == {
        'file_absent({"path":"tmp/scratch.txt"})',
        'backup_exists({"path":"tmp/scratch.txt"})',
    }
