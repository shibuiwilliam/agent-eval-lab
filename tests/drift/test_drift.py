"""8章モジュールの単体テスト（API を呼ばない）。"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from agenteval.drift import attribution as attribution_mod
from agenteval.drift import distribution as dist_mod
from agenteval.drift import fidelity as fidelity_mod
from agenteval.drift import fingerprint as fp_mod
from agenteval.drift.lifecycle import Metrics, TerminalState, log_transition, next_state, read_log
from agenteval.drift.prodstream import ProdStream, paraphrase
from agenteval.env.external import ExternalService


def test_lifecycle_transitions() -> None:
    assert next_state("Created", Metrics(validated=True)) == "Validated"
    assert next_state("Validated", Metrics(flake_rate=0.1)) == "Active"
    assert next_state("Active", Metrics(fidelity=0.5)) == "Stale"
    assert next_state("Active", Metrics(discriminative=0.0)) == "Saturated"
    assert next_state("Active", Metrics(flake_rate=0.9)) == "Hardened"
    assert next_state("Saturated", Metrics(retire_requested=True)) == "Retired"


def test_lifecycle_no_trigger_stays() -> None:
    assert next_state("Created", Metrics(validated=False)) == "Created"
    assert next_state("Active", Metrics(discriminative=0.5)) == "Active"


def test_retired_is_terminal() -> None:
    with pytest.raises(TerminalState):
        next_state("Retired", Metrics())


def test_lifecycle_log_is_append_only(tmp_path: Path) -> None:
    path = tmp_path / "lifecycle.jsonl"
    log_transition("T-001", "Active", "Saturated", Metrics(), path)
    first = read_log(path)
    log_transition("T-002", "Active", "Stale", Metrics(), path)
    second = read_log(path)
    assert len(second) == 2
    assert second[0] == first[0]


def test_fidelity_drops_when_service_changes(tmp_path: Path) -> None:
    fidelity_mod.record_cassettes(
        ExternalService("v1"), ttl_days=30, recorded_at="2026-09-01", directory=tmp_path
    )
    today = date(2026, 9, 13)
    before = fidelity_mod.fidelity_job(ExternalService("v1"), tmp_path, today)
    after = fidelity_mod.fidelity_job(ExternalService("v2"), tmp_path, today)
    assert before.fidelity == 1.0
    assert after.fidelity == 0.0
    assert "price" in after.mismatched_keys


def test_ttl_detection(tmp_path: Path) -> None:
    fidelity_mod.record_cassettes(
        ExternalService("v1"), ttl_days=1, recorded_at="2026-08-01", directory=tmp_path
    )
    assert fidelity_mod.expired_detection_rate(tmp_path, date(2026, 9, 13)) == 1.0


def test_js_divergence_bounds() -> None:
    assert dist_mod.js_divergence({"a": 1.0}, {"a": 1.0}) == 0.0
    assert dist_mod.js_divergence({"a": 1.0}, {"b": 1.0}) == pytest.approx(1.0)


def test_mmd_detects_different_text_groups() -> None:
    a = ["予定を設定して", "予定を入れて", "会議を設定して"] * 4
    b = ["ファイルを消しといて", "ログを削除して", "不要なファイルを消して"] * 4
    result = dist_mod.mmd(a, b, n_permutations=100, seed=1)
    assert result.p_value < 0.2


def test_prodstream_shifts_with_days() -> None:
    stream = ProdStream(seed=1)
    day0 = stream.category_distribution(0, 40)
    day4 = stream.category_distribution(4, 40)
    assert day4.get("expense", 0.0) > day0.get("expense", 0.0)


def test_paraphrase_changes_wording() -> None:
    assert paraphrase("予定を設定して") != "予定を設定して"


def test_attribution_picks_largest_effect() -> None:
    rates = {}
    for combo in attribution_mod.combinations():
        rate = 0.9 - (0.4 if combo["prompt"] else 0.0) - (0.05 if combo["tool"] else 0.0)
        rates[attribution_mod.cell_key(combo)] = rate
    result = attribution_mod.main_effects(rates)
    assert result.predicted_cause == "prompt"
    assert result.top(2)[0] == "prompt"


def test_fingerprint_statistic_is_zero_for_identical() -> None:
    a = fp_mod.Fingerprint(
        model="m", label="a", lengths=[10, 20], prefixes=["x", "y"], tool_choices=["t"]
    )
    b = fp_mod.Fingerprint(
        model="m", label="b", lengths=[10, 20], prefixes=["x", "y"], tool_choices=["t"]
    )
    assert fp_mod.statistic(a, b) == 0.0
