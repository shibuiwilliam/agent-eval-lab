"""W1-F2: Step.effects・Step.policy_decisions・Run.env_kind の既定値付き追加（ADR-031）。

旧形式の最小 trace をテスト内に持つ。data/ には依存しない。
"""

from __future__ import annotations

import json
from typing import Any

from agenteval.core.schema import Effect, PolicyDecision, Run
from agenteval.core.store import trace_hash


def _legacy_run_dict() -> dict[str, Any]:
    """3 フィールドを持たない旧形式の run（JSON 相当）。"""
    return {
        "run_id": "live__T-001__v01_baseline__r0",
        "task_id": "T-001",
        "version_id": "v01_baseline",
        "version_hash": "abc123",
        "mode": "live",
        "seed": 7,
        "injections": [],
        "steps": [
            {
                "i": 0,
                "state_hash_before": "h0",
                "state_hash_after": "h1",
                "assistant_text": "read",
                "tool_calls": [
                    {
                        "id": "c1",
                        "name": "read_file",
                        "args": {"path": "a"},
                        "args_norm": {"path": "a"},
                    }
                ],
                "tool_results": [{"id": "c1", "content": "x", "is_error": False, "bytes": 1}],
                "usage": {"input_tokens": 10, "output_tokens": 5},
                "latency_ms": 12,
                "context_tokens": 100,
                "snapshot_ref": "",
            }
        ],
        "final_text": "done",
        "claims": ["ok"],
        "plan": None,
        "outcome": {"passed": True, "milestone_score": 1.0},
        "cost": {"usd": 0.01, "calls": 1},
        "wall_time_s": 1.5,
        "repeat": 0,
        "parent_run_id": None,
        "divergence_step": None,
        "provenance": "live",
    }


def _load_legacy() -> Run:
    return Run.model_validate_json(json.dumps(_legacy_run_dict()))


def test_legacy_trace_loads_with_defaults() -> None:
    run = _load_legacy()
    assert run.env_kind == "office"
    assert run.steps[0].effects == []
    assert run.steps[0].policy_decisions == []


def _is_subset(raw: Any, dumped: Any) -> bool:
    """raw の項目がすべて dumped に同じ値で残っているか（dumped の追加項目は許す）。"""
    if isinstance(raw, dict):
        return isinstance(dumped, dict) and all(
            k in dumped and _is_subset(v, dumped[k]) for k, v in raw.items()
        )
    if isinstance(raw, list):
        return (
            isinstance(dumped, list)
            and len(raw) == len(dumped)
            and all(_is_subset(a, b) for a, b in zip(raw, dumped, strict=True))
        )
    return bool(raw == dumped)


def test_legacy_roundtrip_keeps_existing_fields() -> None:
    raw = _legacy_run_dict()
    run = _load_legacy()
    dumped = json.loads(run.model_dump_json())
    assert _is_subset(raw, dumped)
    assert Run.model_validate_json(run.model_dump_json()) == run


def test_trace_hash_unchanged_by_new_fields() -> None:
    base = _load_legacy()
    expected = trace_hash(base)
    enriched = base.model_copy(deep=True)
    enriched.env_kind = "repo"
    enriched.steps[0].effects = [
        Effect(kind="fs_write", target="src/a.py", source="fs_diff", via_call_id="c1")
    ]
    enriched.steps[0].policy_decisions = [
        PolicyDecision(
            call_id="c1", capability="fs_read", decision="allow", rule_id="P1.path_scope"
        )
    ]
    assert trace_hash(enriched) == expected


def test_new_fields_roundtrip() -> None:
    run = _load_legacy()
    run.env_kind = "repo"
    run.steps[0].effects = [
        Effect(kind="exec", target="pytest", source="exec_log", canary_hits=["CANARY"])
    ]
    run.steps[0].policy_decisions = [
        PolicyDecision(call_id="c1", capability="exec", decision="deny", rule_id="P2.exec")
    ]
    again = Run.model_validate_json(run.model_dump_json())
    assert again == run
    assert again.env_kind == "repo"
    assert again.steps[0].effects[0].canary_hits == ["CANARY"]
