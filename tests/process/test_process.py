"""4章モジュールの単体テスト（API を呼ばない）。"""

from __future__ import annotations

import math

from agenteval.core.schema import Outcome, Run, Step, ToolCall, ToolResult, Usage
from agenteval.process import budget as budget_mod
from agenteval.process import consistency as consistency_mod
from agenteval.process import metrics as metrics_mod
from agenteval.process import niah as niah_mod
from agenteval.process import plan as plan_mod


def _run(tools: list[str], passed: bool = True, errors: set[int] = frozenset()) -> Run:  # type: ignore[assignment]
    steps = []
    for i, name in enumerate(tools):
        steps.append(
            Step(
                i=i,
                state_hash_before="a",
                state_hash_after="b"
                if name.endswith(("create", "write", "send", "delete"))
                else "a",
                tool_calls=[ToolCall(id=f"t{i}", name=name, args={}, args_norm={})],
                tool_results=[ToolResult(id=f"t{i}", content="{}", is_error=i in errors)],
                usage=Usage(input_tokens=100, output_tokens=10),
                context_tokens=100 * (i + 1),
            )
        )
    return Run(
        run_id="r",
        task_id="T-001",
        version_id="v01_baseline",
        version_hash="h",
        mode="sim",
        seed=0,
        steps=steps,
        outcome=Outcome(passed=passed),
    )


def test_dup_rate_counts_identical_calls() -> None:
    m = metrics_mod.compute(_run(["calendar_search", "calendar_search", "finish"]), l_min=2)
    assert m.dup_rate > 0


def test_verification_rate_is_nan_without_writes() -> None:
    m = metrics_mod.compute(_run(["file_read", "finish"]), l_min=2)
    assert math.isnan(m.verification_rate)


def test_verification_rate_one_when_checked() -> None:
    m = metrics_mod.compute(_run(["file_write", "checks_run", "finish"]), l_min=2)
    assert m.verification_rate == 1.0


def test_recovery_rate() -> None:
    m = metrics_mod.compute(_run(["file_read", "file_read", "finish"], errors={0}), l_min=2)
    assert m.recovery_rate == 1.0


def _with_milestones(run: Run, milestone_steps: dict[str, int]) -> Run:
    run.outcome = Outcome(passed=True, milestone_steps=milestone_steps)
    return run


def test_stop_appropriateness_uses_milestones() -> None:
    """原典 4.2 / ADR-019: 未達の完了宣言と達成後の継続を見る（finish の有無ではない）。"""
    # 全マイルストーン到達 → 適切
    ok = _with_milestones(_run(["file_write", "checks_run", "finish"]), {"M1": 0, "M2": 1})
    assert metrics_mod.compute(ok, l_min=3).stop_appropriate
    assert not metrics_mod.compute(ok, l_min=3).premature_stop

    # 未達なのに終了 → 早すぎる停止
    early = _with_milestones(_run(["file_write", "finish"]), {"M1": 0, "M2": -1})
    assert metrics_mod.compute(early, l_min=2).premature_stop
    assert not metrics_mod.compute(early, l_min=2).stop_appropriate

    # 達成後も動き続ける → 継続しすぎ
    late = _with_milestones(
        _run(["file_write", "checks_run", "file_read", "file_read", "finish"]),
        {"M1": 0, "M2": 1},
    )
    assert metrics_mod.compute(late, l_min=2).overrun
    assert not metrics_mod.compute(late, l_min=2).stop_appropriate


def test_finish_called_is_separate_from_stop_quality() -> None:
    """`finish` を呼んだかは作法の話であって停止の適切さではない（ADR-019）。"""
    assert metrics_mod.compute(_run(["finish"]), l_min=1).finish_called
    assert not metrics_mod.compute(_run(["file_write"]), l_min=1).finish_called


def test_detour_ratio_follows_report_definition() -> None:
    """原典 4.2 / ADR-017: D = L_actual / L_min（1 以上の比）。"""
    m = metrics_mod.compute(_run(["file_read", "file_write", "finish"]), l_min=2)
    assert m.detour_ratio == 1.5
    assert math.isnan(metrics_mod.compute(_run(["finish"]), l_min=None).detour_ratio)


def test_pass_at_k_and_pow_k() -> None:
    assert consistency_mod.pass_at_k(5, 5, 3) == 1.0
    assert consistency_mod.pass_pow_k(5, 5, 3) == 1.0
    assert consistency_mod.pass_at_k(5, 1, 3) > consistency_mod.pass_pow_k(5, 1, 3)
    assert consistency_mod.pass_pow_k(5, 0, 1) == 0.0


def test_action_entropy_zero_for_identical_runs() -> None:
    runs = [_run(["file_read", "finish"]) for _ in range(3)]
    assert consistency_mod.action_entropy(runs) == 0.0


def test_flake_rate_counts_split_outcomes() -> None:
    runs = [_run(["finish"], passed=True), _run(["finish"], passed=False)]
    assert consistency_mod.flake_rate(runs) == 1.0


def test_plan_dag_checks() -> None:
    good = [
        {"id": "s1", "description": "a", "tool": "file_read", "depends_on": []},
        {"id": "s2", "description": "b", "tool": "file_write", "depends_on": ["s1"]},
    ]
    assert plan_mod.check_plan(good).valid
    cyclic = [
        {"id": "s1", "description": "a", "tool": "file_read", "depends_on": ["s2"]},
        {"id": "s2", "description": "b", "tool": "file_write", "depends_on": ["s1"]},
    ]
    assert not plan_mod.check_plan(cyclic).acyclic
    bad_tool = [{"id": "s1", "description": "a", "tool": "nonexistent", "depends_on": []}]
    assert not plan_mod.check_plan(bad_tool).tools_valid
    missing_ref = [{"id": "s1", "description": "a", "tool": "file_read", "depends_on": ["sX"]}]
    assert not plan_mod.check_plan(missing_ref).refs_valid


def test_plan_deviation() -> None:
    run = _run(["file_read", "file_write", "finish"])
    run.plan = [
        {"id": "s1", "description": "a", "tool": "file_read", "depends_on": []},
        {"id": "s2", "description": "b", "tool": "file_write", "depends_on": ["s1"]},
        {"id": "s3", "description": "c", "tool": "checks_run", "depends_on": ["s2"]},
        {"id": "s4", "description": "d", "tool": "finish", "depends_on": ["s3"]},
    ]
    assert plan_mod.deviation(run) > 0
    assert "checks_run" in plan_mod.deviation_sites(run)["skipped_tools"]


def test_niah_auc_and_monotonic() -> None:
    points = [
        niah_mod.RecallPoint(n=0, recall=1.0, runs=3),
        niah_mod.RecallPoint(n=4, recall=0.6, runs=3),
        niah_mod.RecallPoint(n=8, recall=0.2, runs=3),
    ]
    assert 0 < niah_mod.auc(points) < 1
    assert niah_mod.is_monotonic(points)


def test_budget_exceeds_only_above_p95() -> None:
    runs = [_run(["finish"]) for _ in range(5)]
    budgets = budget_mod.p95_by_category(runs, factor=1.0)
    results = budget_mod.check_budgets(runs, budgets)
    assert budget_mod.exceed_rate(results)["rate"] == 0.0
