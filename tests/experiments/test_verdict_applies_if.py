"""実験側の判定（`_common.verdict_from`・`_coding_common.failing_metrics`）と `agenteval verify` の一致（ADR-045）。

結果 JSON・結果ページの verdict と verify の判定が、`applies_if` の N/A でも食い違わないことを固定する。
"""

from __future__ import annotations

import importlib.util
import sys
from types import ModuleType
from typing import Any

import pytest

from agenteval.llm.cost import REPO_ROOT
from agenteval.reports import verify

EXPERIMENT_DIR = REPO_ROOT / "experiments"


def _load(name: str) -> ModuleType:
    sys.path.insert(0, str(EXPERIMENT_DIR))
    try:
        spec = importlib.util.spec_from_file_location(name, EXPERIMENT_DIR / f"{name}.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(EXPERIMENT_DIR))


@pytest.fixture(scope="module")
def common() -> ModuleType:
    return _load("_common")


@pytest.fixture(scope="module")
def coding_common() -> ModuleType:
    return _load("_coding_common")


def _lab(value: Any, provenance: str = "simulated") -> dict[str, Any]:
    return {"value": value, "provenance": provenance}


def _r2_metrics(f_hat: float | None, gain: float = -0.2) -> dict[str, Any]:
    m: dict[str, Any] = {
        "closed_form_mae_k1to4": _lab(0.05),
        "g3_diff_ci_low": _lab(0.1),
        "silent_wrong_monotone_c13": _lab(1),
        "silent_wrong_gain_c13_ci_low": _lab(gain),
        "identity_error_w0": _lab(0.0),
    }
    if f_hat is not None:
        m["c13_f_hat"] = _lab(f_hat, "live")
    return m


def _r2_entry() -> dict[str, Any]:
    return next(e for e in verify.load_registry() if e["id"] == "EC-R2")


@pytest.mark.parametrize("failure_type", ["fail", "negative"])
@pytest.mark.parametrize(
    ("f_hat", "gain", "expected"),
    [
        (0.04, -0.2, "PASS"),  # N/A: 負の gain でも判定に影響しない
        (0.04, 0.3, "PASS"),
        (0.2, 0.3, "PASS"),  # 適用され、満たす
        (0.2, -0.2, None),  # 適用され、満たさない（FAIL か NEGATIVE）
        (None, 0.3, "FAIL"),  # 条件の指標が無い: failure_type に依らず FAIL
    ],
)
def test_ec_r2_verdict_from_matches_verify(
    common: ModuleType, f_hat: float | None, gain: float, expected: str | None, failure_type: str
) -> None:
    entry, metrics = _r2_entry(), _r2_metrics(f_hat, gain)
    verdict, reason = common.verdict_from(entry, metrics, failure_type)
    judged = verify.judge(
        entry, {"metrics": metrics, "provenance": "live", "failure_type": failure_type}
    )
    assert verdict == judged["verdict"]
    if expected is not None:
        assert verdict == expected
    else:
        assert verdict == ("NEGATIVE" if failure_type == "negative" else "FAIL")
    if f_hat is not None and f_hat < 0.15:
        assert (
            f"silent_wrong_gain_c13_ci_low は N/A（検出力不足: c13_f_hat = {f_hat} < 0.15）。"
            "N/A は合格ではない" in reason
        )


def test_ec_r2_failing_metrics_skips_na_and_flags_missing_condition(
    coding_common: ModuleType,
) -> None:
    assert coding_common.failing_metrics("EC-R2", _r2_metrics(0.04)) == []
    assert coding_common.failing_metrics("EC-R2", _r2_metrics(0.2)) == [
        "silent_wrong_gain_c13_ci_low"
    ]
    assert coding_common.failing_metrics("EC-R2", _r2_metrics(None)) == [
        "silent_wrong_gain_c13_ci_low"
    ]
    assert not coding_common.unevaluable_criteria("EC-R2", _r2_metrics(0.04))
    assert coding_common.unevaluable_criteria("EC-R2", _r2_metrics(None))


def test_no_applicable_criterion_is_fail_in_experiment_verdict(
    common: ModuleType, coding_common: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    cond = {"metric": "f", "op": ">=", "value": 0.15}
    meta = {"id": "EX-1", "criteria": [{"metric": "g", "op": ">", "value": 0, "applies_if": cond}]}
    metrics = {"g": _lab(1.0), "f": _lab(0.01, "live")}
    assert common.verdict_from(meta, metrics)[0] == "FAIL"
    assert common.verdict_from(meta, metrics, "negative")[0] == "FAIL"
    assert verify.judge(meta, {"metrics": metrics})["verdict"] == "FAIL"
    monkeypatch.setattr(coding_common, "meta_of", lambda _id: meta)
    assert coding_common.failing_metrics("EX-1", metrics)
    assert coding_common.unevaluable_criteria("EX-1", metrics)


def test_experiment_verdicts_match_verify_for_every_registry_entry(common: ModuleType) -> None:
    """applies_if の無い全実験（office 30 + 章 C の他）で、verdict と理由が従来と同じ。"""
    for entry in verify.load_registry():
        if entry["id"] == "EC-R2":
            continue
        crits = entry["criteria"]
        good = {c["metric"]: _lab(_satisfying(c)) for c in crits}
        assert common.verdict_from(entry, good) == ("PASS", "全基準を満たした。"), entry["id"]
        bad = {k: v for k, v in good.items() if k != crits[0]["metric"]}
        verdict, reason = common.verdict_from(entry, bad)
        assert verdict == "FAIL" and reason == f"未達の基準: {crits[0]['metric']}。", entry["id"]
        assert common.verdict_from(entry, bad, "negative")[0] == "NEGATIVE"
        assert verify.judge(entry, {"metrics": bad})["verdict"] == "FAIL"


def _satisfying(c: dict[str, Any]) -> float:
    t = c["value"]
    return {">=": t, "<=": t, "==": t, ">": t + 1, "<": t - 1}[c["op"]]
