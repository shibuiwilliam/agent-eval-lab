"""基準の適用条件 `applies_if` と N/A の判定（ADR-045）。

条件が真なら通常どおり、偽なら N/A（合格ではない。判定の AND から外す）、条件の指標が無い・来歴が無いなら FAIL、
適用される基準が 0 件でも FAIL。`applies_if` の無い基準の判定は変わらない（回帰）。API は呼ばない。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from agenteval.llm.cost import REPO_ROOT
from agenteval.reports import pages, verify

COND = {"metric": "f_hat", "op": ">=", "value": 0.15}
GAIN = {"metric": "gain", "op": ">", "value": 0, "applies_if": COND}
OTHER = {"metric": "mae", "op": "<=", "value": 0.1}


def _entry(*criteria: dict[str, Any]) -> dict[str, Any]:
    return {"id": "EX-1", "chapter": "C", "title": "t", "criteria": list(criteria)}


def _result(**metrics: Any) -> dict[str, Any]:
    out = {k: {"value": v, "provenance": "live"} for k, v in metrics.items()}
    return {"metrics": out, "provenance": "live"}


def test_condition_true_is_judged_normally() -> None:
    entry = _entry(OTHER, GAIN)
    assert verify.judge(entry, _result(mae=0.05, gain=0.2, f_hat=0.2))["verdict"] == "PASS"
    judged = verify.judge(entry, _result(mae=0.05, gain=-0.1, f_hat=0.2))
    assert judged["verdict"] == "FAIL" and "gain" in judged["detail"]
    assert judged["criteria"][1]["status"] == "fail"


def test_condition_false_makes_the_criterion_na_and_drops_it_from_the_and() -> None:
    entry = _entry(OTHER, GAIN)
    # gain は負でも、N/A なので判定に影響しない。N/A は合格ではないと detail に明記する
    judged = verify.judge(entry, _result(mae=0.05, gain=-0.3, f_hat=0.04))
    assert judged["verdict"] == "PASS"
    assert "N/A" in judged["detail"] and "合格ではない" in judged["detail"]
    row = judged["criteria"][1]
    assert row["status"] == "na" and row["ok"] is None
    assert pages.na_text(row["na"]) == "N/A（検出力不足: f_hat = 0.04 < 0.15）"
    # 残りの基準が落ちれば、N/A があっても FAIL
    assert verify.judge(entry, _result(mae=0.5, gain=0.3, f_hat=0.04))["verdict"] == "FAIL"


def test_na_does_not_hide_a_declared_negative() -> None:
    entry = _entry(OTHER, GAIN)
    res = _result(mae=0.5, gain=0.3, f_hat=0.04) | {"failure_type": "negative"}
    assert verify.judge(entry, res)["verdict"] == "NEGATIVE"


def test_condition_metric_missing_is_fail() -> None:
    entry = _entry(OTHER, GAIN)
    judged = verify.judge(entry, _result(mae=0.05, gain=0.3))
    assert judged["verdict"] == "FAIL" and "f_hat" in judged["detail"]
    assert judged["criteria"][1]["status"] == "error"
    # 宣言された NEGATIVE よりも FAIL（実装の不備）を優先する
    res = _result(mae=0.05, gain=0.3) | {"failure_type": "negative"}
    assert verify.judge(entry, res)["verdict"] == "FAIL"


def test_condition_metric_without_provenance_is_fail() -> None:
    entry = _entry(OTHER, GAIN)
    res = _result(mae=0.05, gain=0.3)
    res["metrics"]["f_hat"] = {"value": 0.5}  # 来歴ラベルが無い
    assert verify.judge(entry, res)["verdict"] == "FAIL"
    # 判定の前段で弾かれても、基準単体の評価でも error になる
    assert pages.evaluate_criterion(GAIN, res["metrics"])["status"] == "error"
    res["metrics"]["f_hat"] = 0.5  # 値だけ（ラベル無し）
    assert pages.evaluate_criterion(GAIN, res["metrics"])["status"] == "error"


def test_no_applicable_criterion_is_fail_not_pass() -> None:
    entry = _entry(GAIN)
    judged = verify.judge(entry, _result(gain=0.3, f_hat=0.01))
    assert judged["verdict"] == "FAIL" and "適用される基準が 1 つも無い" in judged["detail"]


def test_na_is_visible_in_summary_and_criteria_table(tmp_path: Path) -> None:
    entry = _entry(OTHER, GAIN)
    result = _result(mae=0.05, gain=-0.3, f_hat=0.04)
    judged = verify.judge(entry, result)
    row = {
        "id": "EX-1",
        "chapter": "C",
        "title": "t",
        "verdict": judged["verdict"],
        "detail": judged["detail"],
        "provisional": False,
        "provenance": "live",
        "usd": 0.0,
        "key_metrics": judged["criteria"],
    }
    ja = verify.write_summary([row], tmp_path / "s.md").read_text(encoding="utf-8")
    en = verify.write_summary_en([row], tmp_path / "s.en.md").read_text(encoding="utf-8")
    assert "gain=-0.3 N/A（検出力不足: f_hat = 0.04 < 0.15）" in ja
    assert "EX-1.gain" in ja and "合格ではなく" in ja
    assert "gain=-0.3 N/A (insufficient power: f_hat = 0.04 < 0.15)" in en
    assert "not a pass" in en

    meta = entry | {"hypothesis": "h", "provenance": "live"}
    page = pages.render(meta | {"id": "EX-1"}, result | {"verdict": "PASS", "verdict_reason": "r"})
    assert "gain > 0（適用条件: f_hat >= 0.15）" in page  # 5 項目の基準
    assert "| N/A（検出力不足: f_hat = 0.04 < 0.15） |" in page  # 結果の表
    assert "N/A は合格ではない" in page  # 判定欄の注記
    page_en = pages.render(meta | {"id": "EX-1"}, result | {"verdict": "PASS"}, "en", {})
    assert "N/A (insufficient power: f_hat = 0.04 < 0.15)" in page_en


def test_summary_without_na_is_unchanged(tmp_path: Path) -> None:
    entry = _entry(OTHER)
    judged = verify.judge(entry, _result(mae=0.05))
    row = {
        "id": "EX-1",
        "chapter": "C",
        "title": "t",
        "verdict": judged["verdict"],
        "detail": judged["detail"],
        "provisional": False,
        "provenance": "live",
        "usd": 0.0,
        "key_metrics": judged["criteria"],
    }
    text = verify.write_summary([row], tmp_path / "s.md").read_text(encoding="utf-8")
    assert "mae=0.05○" in text and "N/A" not in text
    assert judged["detail"] == "全基準を満たした"


def test_load_registry_rejects_unknown_keys_and_malformed_applies_if(tmp_path: Path) -> None:
    def load(criterion: dict[str, Any]) -> None:
        path = tmp_path / "r.yaml"
        path.write_text(yaml.safe_dump([_entry(criterion)]), encoding="utf-8")
        verify.load_registry(path)

    load(GAIN)  # 正しい形は通る
    with pytest.raises(ValueError, match="未知のキー"):
        load({"metric": "m", "op": ">", "value": 0, "applies_when": COND})
    with pytest.raises(ValueError, match="applies_if"):
        load({"metric": "m", "op": ">", "value": 0, "applies_if": {"metric": "f_hat"}})
    with pytest.raises(ValueError, match="演算子"):
        load({"metric": "m", "op": ">", "value": 0, "applies_if": COND | {"op": "~"}})


def test_real_registry_only_ec_r2_has_applies_if_and_judgments_are_unchanged() -> None:
    registry = verify.load_registry()
    with_cond = [
        (e["id"], c["metric"]) for e in registry for c in e["criteria"] if "applies_if" in c
    ]
    assert with_cond == [("EC-R2", "silent_wrong_gain_c13_ci_low")]
    cond = next(
        c for e in registry if e["id"] == "EC-R2" for c in e["criteria"] if "applies_if" in c
    )["applies_if"]
    assert cond == {"metric": "c13_f_hat", "op": ">=", "value": 0.15}
    # applies_if の無い基準だけの実験は、旧実装と同じ規則（全基準が AND、値が無ければ未達）で判定される
    for entry in registry:
        if entry["id"] == "EC-R2":
            continue
        good = {
            c["metric"]: {"value": _satisfying(c), "provenance": "simulated"}
            for c in entry["criteria"]
        }
        assert verify.judge(entry, {"metrics": good})["verdict"] == "PASS", entry["id"]
        bad = dict(good)
        bad.pop(entry["criteria"][0]["metric"])
        judged = verify.judge(entry, {"metrics": bad})
        assert judged["verdict"] == "FAIL", entry["id"]
        assert all(r["status"] in ("pass", "fail") for r in judged["criteria"])


def _satisfying(criterion: dict[str, Any]) -> float:
    target = criterion["value"]
    return {
        ">=": target,
        "<=": target,
        "==": target,
        ">": target + 1,
        "<": target - 1,
    }[criterion["op"]]


def test_registry_draft_ec_r2_matches_registry() -> None:
    draft = yaml.safe_load((REPO_ROOT / "docs/plan/coding/registry_draft.yaml").read_text("utf-8"))
    draft = draft if isinstance(draft, list) else draft.get("experiments", [])
    d = next(e for e in draft if e["id"] == "EC-R2")
    r = next(e for e in verify.load_registry() if e["id"] == "EC-R2")
    pick = lambda e: next(c for c in e["criteria"] if c["metric"] == "silent_wrong_gain_c13_ci_low")
    assert pick(d) == pick(r)
