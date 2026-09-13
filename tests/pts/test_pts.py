"""3章モジュールの単体テスト（API を呼ばない）。"""

from __future__ import annotations

import pytest

from agenteval.core.schema import Change, Run, Step, ToolCall
from agenteval.pts import change as change_mod
from agenteval.pts import coverage as coverage_mod
from agenteval.pts import kpi as kpi_mod
from agenteval.pts import pyramid as pyramid_mod
from agenteval.pts import selector as selector_mod
from agenteval.pts.sprt import SPRT, error_rates, fixed_n_for_power


def _run(task_id: str, tools: list[str], passed: bool = True) -> Run:
    from agenteval.core.schema import Outcome

    steps = [
        Step(
            i=i,
            state_hash_before="a",
            state_hash_after="a",
            tool_calls=[ToolCall(id=f"t{i}", name=name, args={}, args_norm={})],
        )
        for i, name in enumerate(tools)
    ]
    return Run(
        run_id=f"{task_id}-r",
        task_id=task_id,
        version_id="v01_baseline",
        version_hash="h",
        mode="sim",
        seed=0,
        steps=steps,
        outcome=Outcome(passed=passed),
    )


def test_diff_versions_detects_kinds() -> None:
    changes = {c.kind for c in change_mod.diff_versions("v01_baseline", "v06_toolschema_v2")}
    assert "tool" in changes
    changes = {c.kind for c in change_mod.diff_versions("v01_baseline", "v09_model_swap")}
    assert "model" in changes
    changes = {c.kind for c in change_mod.diff_versions("v01_baseline", "v02_dateformat")}
    assert "prompt" in changes


def test_prompt_diff_is_section_scoped() -> None:
    change = next(
        c for c in change_mod.diff_versions("v01_baseline", "v02_dateformat") if c.kind == "prompt"
    )
    assert change.components == {"date_format"}


def test_coverage_units_and_candidates() -> None:
    runs = [
        _run("T-a", ["calendar_search", "calendar_create"]),
        _run("T-b", ["mail_search", "mail_send"]),
    ]
    coverage = coverage_mod.coverage_map(runs)
    assert "tool:calendar_search" in coverage["T-a"]
    change = Change(kind="tool", base="v1", target="v2", components={"mail_send"})
    assert coverage_mod.candidates(change, coverage) == {"T-b"}


def test_model_change_selects_everything() -> None:
    runs = [_run("T-a", ["calendar_search"]), _run("T-b", ["mail_search"])]
    coverage = coverage_mod.coverage_map(runs)
    change = Change(kind="model", base="v1", target="v2", components={"model"})
    assert coverage_mod.candidates(change, coverage) == {"T-a", "T-b"}


def test_pyramid_levels_are_ordered() -> None:
    assert pyramid_mod.decide_level(Change(kind="code", base="a", target="b")) == "L1"
    assert pyramid_mod.decide_level(Change(kind="prompt", base="a", target="b")) == "L2"
    assert pyramid_mod.decide_level(Change(kind="tool", base="a", target="b")) == "L3"
    assert pyramid_mod.decide_level(Change(kind="model", base="a", target="b")) == "L4"
    assert pyramid_mod.at_least("L4", "L2")
    assert not pyramid_mod.at_least("L1", "L3")


def test_beta_posterior_and_entropy() -> None:
    assert selector_mod.beta_posterior(0, 0) == 0.5
    assert selector_mod.beta_posterior(4, 0) > selector_mod.beta_posterior(1, 1)
    assert selector_mod.entropy(0.5) == pytest.approx(1.0)
    assert selector_mod.entropy(0.99) < 0.1


def test_similarity_of_tool_sequences() -> None:
    assert selector_mod.similarity(["a", "b"], ["a", "b"]) == 1.0
    assert selector_mod.similarity(["a", "b"], ["c", "d"]) == 0.0


def test_inviolable_is_always_selected() -> None:
    runs = [_run("T-005", ["file_backup", "file_delete"]), _run("T-006", ["calendar_search"])]
    coverage = coverage_mod.coverage_map(runs)
    change = Change(kind="tool", base="v1", target="v2", components={"mail_send"})
    selection = selector_mod.select(["T-005", "T-006"], runs, change, coverage, budget_ratio=0.01)
    assert "T-005" in selection.selected
    assert selection.reasons["T-005"] == "inviolable"


def test_escape_rate_counts_missed_flips() -> None:
    selection = selector_mod.Selection(
        selected=["T-a"], skipped=["T-b"], budget=0.0, expected_escape=0.0
    )
    assert kpi_mod.escape_rate(selection, {"T-a", "T-b"}) == 0.5
    assert kpi_mod.escape_rate(selection, set()) == 0.0


def test_sprt_decides_and_respects_bounds() -> None:
    test = SPRT(p0=0.5, p1=0.9)
    decisions = [test.update(True) for _ in range(10)]
    assert "accept" in decisions
    test.reset()
    decisions = [test.update(False) for _ in range(10)]
    assert "reject" in decisions


def test_sprt_error_rates_are_near_nominal() -> None:
    result = error_rates(trials=500, seed=1)
    assert result["empirical_alpha"] <= 0.12
    assert result["empirical_beta"] <= 0.15


def test_fixed_n_is_positive() -> None:
    assert fixed_n_for_power(0.5, 0.9, 0.05, 0.1) > 0
