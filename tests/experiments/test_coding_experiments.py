"""章 C（第 1 段）の実験スクリプト・registry・CLI・コーパス計画のテスト（W1-I8）。

live API を呼ばない。run を作るテストは `LLMClient(mode="sim")` と FakeExecutor だけを使い、生成物は tmp_path に書く。
EC-S1・EC-O2 は W1-I6 の実装に依存する部分（カタログ・4 検査の関数）を呼ばず、集計と入れ物だけを調べる。
"""

from __future__ import annotations

import importlib.util
import json
import math
import sys
import tempfile
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
import pytest
import yaml
from typer.testing import CliRunner

from agenteval.cli import app
from agenteval.core import coding_registry
from agenteval.core.coding_store import save_coding_run
from agenteval.core.coding_types import (
    ACC_FINISHED,
    ACC_HIDDEN,
    ACC_VISIBLE,
    REPO_FIXTURE_ROOT,
    CodingOutcome,
    ExecResult,
    SandboxMounts,
)
from agenteval.core.schema import Cost, Outcome, Run, Step, ToolCall, ToolResult, Usage
from agenteval.econ.roi import load_theta_space, sample_thetas
from agenteval.env import repo as repo_mod
from agenteval.env.sandbox import FakeCall, fake_executor_factory
from agenteval.llm.cost import REPO_ROOT, LedgerEntry, load_pricing
from agenteval.reports import coding_corpus as cc
from agenteval.reports import pages, verify

EXPERIMENT_DIR = REPO_ROOT / "experiments"
HAIKU = "claude-haiku-4-5-20251001"
RUNNER = CliRunner()


def _load_script(name: str) -> ModuleType:
    """実験スクリプトを import する（experiments/ は pytest の path に無い）。"""
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


# ---------------------------------------------------------------------------
# registry・verify
# ---------------------------------------------------------------------------

CHAPTER_C_IDS = ["EC-S1", "EC-O2", "EC-B2P", "EC-B2", "EC-R1", "EC-R2"]


def test_registry_has_six_chapter_c_entries_matching_the_draft() -> None:
    entries = {e["id"]: e for e in verify.load_registry()}
    draft = yaml.safe_load(
        (REPO_ROOT / "docs" / "plan" / "coding" / "registry_draft.yaml").read_text(encoding="utf-8")
    )
    assert [e["id"] for e in draft] == CHAPTER_C_IDS
    for expected in draft:
        written = {
            **entries[expected["id"]],
            "criteria": [
                {k: v for k, v in c.items() if k != "provisional"}
                for c in entries[expected["id"]]["criteria"]
            ],
        }
        assert (
            written == expected
        )  # 下書きの写し（暫定の印 provisional を除く）で、1 字も変えていない
        assert expected["chapter"] == "C"
        assert expected["provenance"] in ("live", "replay", "simulated")
        assert (REPO_ROOT / expected["script"]).exists()
        assert expected["budget_usd"] == 0  # 実験スクリプトは live の run を作らない


def test_registry_keeps_the_30_existing_entries_first() -> None:
    ids = [e["id"] for e in verify.load_registry()]
    assert len(ids) == 36 and ids[-6:] == CHAPTER_C_IDS
    assert not any(str(e["chapter"]) == "C" for e in verify.load_registry()[:30])


def test_chapter_c_rows_are_pending_without_results_and_have_a_footnote(tmp_path: Path) -> None:
    rows = []
    for entry in verify.load_registry():
        judged = verify.judge(entry, verify.load_result(entry["id"], tmp_path))
        rows.append(
            {
                "id": entry["id"],
                "chapter": entry.get("chapter"),
                "title": entry.get("title", ""),
                "verdict": judged["verdict"],
                "detail": judged["detail"],
                "provenance": entry.get("provenance", ""),
                "usd": 0.0,
                "key_metrics": judged["criteria"],
            }
        )
    c_rows = [r for r in rows if str(r["chapter"]) == "C"]
    assert [r["verdict"] for r in c_rows] == ["PENDING"] * 6
    ja = verify.write_summary(rows, tmp_path / "summary.md").read_text(encoding="utf-8")
    en = verify.write_summary_en(rows, tmp_path / "summary.en.md").read_text(encoding="utf-8")
    assert "| [EC-S1](EC-S1.md) | C |" in ja
    assert "章 C = コーディングエージェント" in ja and "EC-B2、EC-R1、EC-R2" in ja
    assert "Chapter C = coding agent" in en
    # 章 C が無い表には注記を足さない（既存の行・体裁を変えない）
    only_old = [r for r in rows if str(r["chapter"]) != "C"]
    assert verify._chapter_c_footnote(only_old, "ja") == []


def test_design_method_survives_regeneration(tmp_path: Path) -> None:
    """finalize が書くページの「方法」は、設計者のページから引き継がれ、再生成しても変わらない。"""
    sys.path.insert(0, str(EXPERIMENT_DIR))
    try:
        common = _load_script("_coding_common")
    finally:
        sys.path.remove(str(EXPERIMENT_DIR))
    designer = common.design_method("EC-B2P")
    assert "課金の割り振り" in designer and "FAIL と NEGATIVE の区別" in designer
    meta = {"id": "EC-X", "title": "t", "criteria": []}
    page = pages.render(meta, {"method": designer, "metrics": {}, "notes": []})
    match = common._METHOD_RE.search(page)
    assert match is not None and match.group(1).strip() == designer


def test_failing_metrics_reports_missing_values_as_unmet() -> None:
    common = _load_script("_coding_common")
    ok = {"w_rate_diff_ci_low": {"value": 0.1, "provenance": "live"}}
    failed = common.failing_metrics("EC-R1", ok)
    assert "w_rate_diff_ci_low" not in failed and "rob_c01_over_c13" in failed  # 欠けた指標は未達


# ---------------------------------------------------------------------------
# コーパス計画・見積り・run の状態
# ---------------------------------------------------------------------------


def test_corpus_plan_counts_and_seeds() -> None:
    plan = cc.load_plan()
    assert {s: cc.stage_seed(plan, s) for s in plan["stages"]} == {
        "offline": 20261100,
        "pilot": 20261101,
        "opcheck": 20261102,
        "corpus": 20261103,
    }
    assert len(cc.plan_units(plan, "pilot")) == 96
    assert len(cc.plan_units(plan, "opcheck")) == 10
    corpus = cc.plan_units(plan, "corpus")
    assert len(corpus) == 356
    assert len({u.run_id for u in corpus}) == 356
    assert all("__corpus__" in u.run_id for u in corpus)
    with pytest.raises(cc.PlanError):
        cc.plan_units(plan, "offline")  # sim の段は計画から展開できない
    with pytest.raises(cc.PlanError):
        cc.stage_of(plan, "nope")


def test_estimate_reproduces_task_md_reference_within_tolerance(tmp_path: Path) -> None:
    plan = cc.load_plan()
    ref = plan["estimate"]["reference_usd_per_run"]
    est = cc.estimate_stage(plan, "corpus", data_root=tmp_path, budget_usd=None, ledger_usd=0.0)
    by = est["by_version"]
    assert by["c01_baseline"]["history_cache"] is True
    assert by["c01o_no_history_cache"]["history_cache"] is False
    # off は on の約 2.2 倍（EC-B2.md）。TASK.md 6.2 節の参照値から 10% 以内
    assert by["c01_baseline"]["usd_per_run"] == pytest.approx(ref["history_cache_on"], rel=0.10)
    assert by["c01o_no_history_cache"]["usd_per_run"] == pytest.approx(
        ref["history_cache_off"], rel=0.10
    )
    assert est["usd_conservative"] == pytest.approx(est["usd_nominal"] * 1.5, abs=0.01)
    assert est["usd_nominal"] == pytest.approx(16.5, rel=0.10)  # TASK.md 6.2 節（約 16.5 USD）
    assert est["remaining_runs"] == 356 and est["budget_fits_remaining_conservative"] is None


def test_estimate_budget_check(tmp_path: Path) -> None:
    plan = cc.load_plan()
    tight = cc.estimate_stage(plan, "pilot", data_root=tmp_path, budget_usd=5.0, ledger_usd=3.8)
    assert tight["budget_fits_remaining_conservative"] is False
    ok = cc.estimate_stage(plan, "pilot", data_root=tmp_path, budget_usd=11.3, ledger_usd=3.8)
    assert ok["budget_fits_remaining_conservative"] is True


def _fake_run(run_id: str, task_id: str, version_id: str, repeat: int, **kw: Any) -> Run:
    return Run(
        run_id=run_id,
        task_id=task_id,
        version_id=version_id,
        version_hash="h",
        mode="live",
        seed=20261103,
        repeat=repeat,
        provenance="live",
        env_kind="repo",
        **kw,
    )


def _save_corpus_runs(
    data_root: Path, versions: Sequence[str], *, stage: str = "corpus", partial: Sequence[str] = ()
) -> None:
    plan = cc.load_plan()
    for unit in cc.plan_units(plan, stage, versions=set(versions)):
        run = _fake_run(unit.run_id, unit.task_id, unit.version_id, unit.repeat)
        save_coding_run(run, None, partial=unit.run_id in partial, data_root=data_root)


def test_require_stage_runs_stops_with_guidance_when_runs_are_missing(tmp_path: Path) -> None:
    with pytest.raises(cc.StageIncompleteError) as err:
        cc.require_stage_runs("corpus", versions={"c12_null"}, data_root=tmp_path)
    message = str(err.value)
    assert "48/48 件欠けている" in message and "coding corpus" in message and "G2" in message
    # 作らない: 何も書かれていない
    assert not list(tmp_path.rglob("*.json"))


def test_require_stage_runs_reads_only_the_stage_and_requires_completeness(tmp_path: Path) -> None:
    _save_corpus_runs(tmp_path, ["c12_null"])
    runs = cc.require_stage_runs("corpus", versions={"c12_null"}, data_root=tmp_path)
    assert len(runs) == 48 and all(r.run_id.split("__")[3] == "corpus" for r in runs)


def test_require_stage_runs_stops_on_partial_runs(tmp_path: Path) -> None:
    plan = cc.load_plan()
    first = cc.plan_units(plan, "corpus", versions={"c12_null"})[0].run_id
    _save_corpus_runs(tmp_path, ["c12_null"], partial=[first])
    with pytest.raises(cc.PartialRunsError) as err:
        cc.require_stage_runs("corpus", versions={"c12_null"}, data_root=tmp_path)
    assert "PENDING" in str(err.value) and first in str(err.value)


def test_unit_states_and_shards(tmp_path: Path) -> None:
    plan = cc.load_plan()
    units = cc.plan_units(plan, "opcheck")
    run = _fake_run(units[0].run_id, units[0].task_id, units[0].version_id, 0)
    save_coding_run(run, None, data_root=tmp_path)
    states = cc.unit_states(units, data_root=tmp_path)
    assert (len(states["done"]), len(states["partial"]), len(states["missing"])) == (1, 0, 9)
    shards = [cc.shard_units(units, (i, 4)) for i in range(4)]
    assert sorted(u.run_id for s in shards for u in s) == sorted(u.run_id for u in units)
    assert cc.parse_shard("2/4") == (2, 4)
    for bad in ("4/4", "x", "-1/4", "0/0"):
        with pytest.raises(cc.PlanError):
            cc.parse_shard(bad)


def test_run_stage_skips_done_stops_on_budget_and_reports_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agenteval.agent import coding_loop
    from agenteval.llm.cost import BudgetExceeded

    plan = cc.load_plan()
    units = cc.plan_units(plan, "opcheck")
    save_coding_run(
        _fake_run(units[0].run_id, units[0].task_id, units[0].version_id, 0),
        None,
        data_root=tmp_path,
    )
    save_coding_run(
        _fake_run(units[1].run_id, units[1].task_id, units[1].version_id, 0),
        None,
        partial=True,
        data_root=tmp_path,
    )
    calls: list[str] = []

    def fake_run(task: Any, version: Any, opts: Any) -> tuple[Run, CodingOutcome]:
        run_id = coding_registry.coding_run_id(task.id, version.id, opts.stage, opts.repeat)
        calls.append(run_id)
        assert opts.mode == "record" and opts.seed == 20261102  # 段の seed（塩付き record）
        if len(calls) == 3:
            raise BudgetExceeded("予算停止（テスト）")
        run = _fake_run(run_id, task.id, version.id, opts.repeat, cost=Cost(usd=0.05))
        run.outcome = Outcome(passed=True)
        return run, None  # type: ignore[return-value]

    monkeypatch.setattr(coding_loop, "run_coding_task", fake_run)
    summary = cc.run_stage(plan, "opcheck", data_root=tmp_path, guard=object())  # type: ignore[arg-type]
    assert summary["skipped_done"] == 1 and summary["blocked_partial"] == [units[1].run_id]
    assert summary["ran"] == 2 and summary["usd"] == pytest.approx(0.10)
    assert "BudgetExceeded" in summary["stopped"]
    assert units[1].run_id not in calls  # 途中終了の run は触らない


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_coding_corpus_dry_run_prints_estimate_without_calling_api() -> None:
    result = RUNNER.invoke(app, ["coding", "corpus", "--stage", "opcheck"])
    assert result.exit_code == 0, result.output
    out = json.loads(result.output)
    assert out["runs"] == 10 and out["usd_conservative"] > out["usd_nominal"] > 0
    assert set(out["by_version"]) == {"c13_minimal_fix"}


def test_cli_coding_corpus_live_needs_approval_key_and_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refused = RUNNER.invoke(app, ["coding", "corpus", "--stage", "pilot", "--live"])
    assert refused.exit_code != 0 and "--approved" in refused.output
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    no_key = RUNNER.invoke(
        app, ["coding", "corpus", "--stage", "pilot", "--live", "--approved", "G1"]
    )
    assert no_key.exit_code != 0 and "ANTHROPIC_API_KEY" in no_key.output
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.delenv("AGENTEVAL_BUDGET_USD", raising=False)
    no_budget = RUNNER.invoke(
        app, ["coding", "corpus", "--stage", "pilot", "--live", "--approved", "G1"]
    )
    assert no_budget.exit_code != 0 and "AGENTEVAL_BUDGET_USD" in no_budget.output
    bad_mode = RUNNER.invoke(app, ["coding", "corpus", "--stage", "pilot", "--mode", "auto"])
    assert bad_mode.exit_code != 0  # auto は拒否


def test_cli_coding_report_opcheck_requires_only_spec() -> None:
    refused = RUNNER.invoke(app, ["coding", "report", "--stage", "opcheck"])
    assert refused.exit_code != 0 and "ADR-037" in refused.output


def test_cli_coding_run_rejects_sim_without_script_and_auto() -> None:
    res = RUNNER.invoke(
        app, ["coding", "run", "--version", "c01_baseline", "--task", "CT-tillcalc-001"]
    )
    assert res.exit_code != 0
    res2 = RUNNER.invoke(
        app,
        [
            "coding",
            "run",
            "--version",
            "c01_baseline",
            "--task",
            "CT-tillcalc-001",
            "--mode",
            "auto",
        ],
    )
    assert res2.exit_code != 0


def test_cli_exp_dry_run_for_chapter_c_and_old_experiments() -> None:
    res = RUNNER.invoke(app, ["exp", "ec_r1", "--dry-run"])
    assert res.exit_code == 0, res.output
    out = json.loads(res.output)
    assert out["id"] == "EC-R1" and out["inputs"]["creates_runs"] == 0
    assert out["inputs"]["planned"] == 24 * 6 * 2 + 24 * 2 and out["budget_usd"] == 0
    res_b2 = RUNNER.invoke(app, ["exp", "EC-B2", "--dry-run"])
    assert json.loads(res_b2.output)["inputs"]["creates_simulated_runs"]["count"] == 2
    old = RUNNER.invoke(app, ["exp", "e3_1", "--dry-run"])
    assert old.exit_code == 0 and json.loads(old.output)["id"] == "E3-1"


def test_cli_exp_live_experiment_stops_with_guidance_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """run が無い live の実験（EC-R1）は案内付きで止まり、結果 JSON を書かない（PENDING のまま）。"""
    from agenteval.reports import runner

    monkeypatch.setattr(runner, "RESULT_DIR", tmp_path / "results")
    # 実験スクリプトは import 時に require_stage_runs を束縛する。段の run が無い状況をここで作る
    monkeypatch.setattr(
        cc,
        "require_stage_runs",
        lambda *a, **k: (_ for _ in ()).throw(
            cc.StageIncompleteError("run が 336/336 件欠けている（テスト）")
        ),
    )
    res = RUNNER.invoke(app, ["exp", "ec_r1"])
    assert res.exit_code == 1 and "[停止]" in res.output
    assert not (tmp_path / "results").exists()


# ---------------------------------------------------------------------------
# stage_report（パイロット・操作確認）
# ---------------------------------------------------------------------------


def _outcome_run(
    run_id: str, task_id: str, version_id: str, repeat: int, cls: str, *, usd: float = 0.04
) -> Run:
    finished = cls != "F"
    run = _fake_run(
        run_id,
        task_id,
        version_id,
        repeat,
        cost=Cost(usd=usd),
        steps=[
            Step(
                i=0,
                state_hash_before="a",
                state_hash_after="b",
                usage=Usage(
                    input_tokens=0, cache_read_input_tokens=2500, cache_creation_input_tokens=700
                ),
            ),
            Step(
                i=1,
                state_hash_before="b",
                state_hash_after="c",
                usage=Usage(
                    input_tokens=10, cache_read_input_tokens=3200, cache_creation_input_tokens=500
                ),
                tool_calls=[ToolCall(id="c1", name="run_tests", args={}, args_norm={})],
                tool_results=[ToolResult(id="c1", content=json.dumps({"timed_out": cls == "F"}))],
            ),
        ],
    )
    run.outcome = Outcome(
        passed=cls == "S",
        acceptance_results={
            ACC_FINISHED: finished,
            ACC_VISIBLE: cls in ("S", "W"),
            ACC_HIDDEN: cls == "S",
        },
    )
    return run


def test_stage_report_pilot_vs_only_spec(tmp_path: Path) -> None:
    plan = cc.load_plan()
    units = cc.plan_units(plan, "opcheck")
    classes = ["S", "W", "F", "S", "W", "W", "S", "F", "S", "W"]
    for unit, cls in zip(units, classes, strict=True):
        save_coding_run(
            _outcome_run(unit.run_id, unit.task_id, unit.version_id, unit.repeat, cls),
            None,
            data_root=tmp_path,
        )
    only = cc.stage_report(plan, "opcheck", only_spec=True, data_root=tmp_path)
    block = only["versions"]["c13_minimal_fix"]
    assert set(block) == {"n_runs", "spec"}  # 合格率・W 率・費用は出さない（ADR-037）
    assert block["spec"]["n_bugfix_runs"] == 10 and "spec_literal_rate" in block["spec"]
    assert "pi_W" not in json.dumps(only) and "usd_per_run_mean" not in json.dumps(only)
    full = cc.stage_report(plan, "opcheck", data_root=tmp_path)
    v = full["versions"]["c13_minimal_fix"]
    assert v["bugfix"]["S"] == 4 and v["bugfix"]["W"] == 4 and v["bugfix"]["F"] == 2
    assert v["finish_not_called_rate"] == 0.2 and v["timeout_run_rate"] == 0.2
    assert v["usd_per_run_mean"] == pytest.approx(0.04)
    assert v["prefix_tokens_estimate"] == 2500 and v["history_cache_inactive_runs"] == 0


def test_history_cache_inactive_counts_runs_whose_history_read_is_prefix_only() -> None:
    a = _outcome_run("cd__CT-a-001__c01_baseline__pilot__r0", "CT-a-001", "c01_baseline", 0, "S")
    b = a.model_copy(deep=True)
    b.steps[1].usage.cache_read_input_tokens = 2500  # 履歴が読まれていない（接頭辞だけ）
    assert cc.estimate_prefix_tokens([a, b]) == 2500
    assert not cc.history_cache_inactive(a, 2500)
    assert cc.history_cache_inactive(b, 2500)
    assert (
        cc.estimate_prefix_tokens([_outcome_run("r", "t", "v", 0, "S").model_copy(deep=True)])
        == 2500
    )


def test_sigma_materials_follow_ec_r1_formulas() -> None:
    tasks = coding_registry.load_coding_tasks()
    ids = sorted(t for t, d in tasks.items() if d.kind == "bugfix")[:4]
    runs: list[Run] = []
    for i, tid in enumerate(ids):
        # タスク 0,1 は 2 反復とも W、2,3 は S（異質性あり）。費用は反復で 0.01 違う
        cls = "W" if i < 2 else "S"
        for r in range(2):
            runs.append(
                _outcome_run(
                    f"cd__{tid}__c01_baseline__pilot__r{r}",
                    tid,
                    "c01_baseline",
                    r,
                    cls,
                    usd=0.04 + 0.01 * r,
                )
            )
    mat = cc._sigma_materials(runs, tasks)
    assert mat["w_rate_mean"] == 0.5
    # w_hat = [1,1,0,0]。Var(ddof=1) = 1/3。タスク内の二項の揺れ = mean(w(1-w)) / (m-1) = 0
    assert mat["tau_w_sq"] == pytest.approx(1 / 3, abs=1e-4)
    first = mat["sigma_d_grid"][0]
    sigma = math.sqrt(1 / 3 + (0.25 + 0.6 * 0.4) / 6.0)
    assert first["sigma_d"] == pytest.approx(sigma, abs=1e-3)
    assert first["mde"] == pytest.approx(2.8 * sigma / math.sqrt(24), abs=1e-3)
    assert mat["sigma_c_usd"] == pytest.approx(0.01 / math.sqrt(2), abs=1e-4)


# ---------------------------------------------------------------------------
# EC-B2P（既存の live 軌跡の再計算。data/fixtures/traces の 3 本と台帳の行）
# ---------------------------------------------------------------------------

FIXTURE_TRACES = REPO_ROOT / "data" / "fixtures" / "traces"


def _fixture_ledger() -> list[LedgerEntry]:
    return [
        LedgerEntry.model_validate_json(line)
        for line in (FIXTURE_TRACES / "ledger_rows.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_b2p_metrics_on_fixture_traces() -> None:
    b2p = _load_script("ec_b2p_context_cost")
    runs = b2p.load_live_traces(FIXTURE_TRACES)
    assert len(runs) == 3
    out = b2p.compute_metrics(runs, _fixture_ledger(), load_pricing())
    m = {k: v["value"] for k, v in out["metrics"].items()}
    assert m["ledger_match_rate"] == 1.0 and m["runs_fully_matched"] == 3
    assert m["ledger_identity_error"] <= 1e-9 and m["allocation_identity_error"] <= 1e-9
    assert m["negative_increment_rate"] == 0.0
    assert m["unmatched_ledger_rows"] >= 1  # 再実行の行を含む run を 1 本以上入れてある（K3）
    assert m["naive_ledger_identity_error"] > 0.001  # 素朴な照合は落ちる
    # 植込みは適格な組にだけ適用され、すべて検出される
    cases = m["perturbation_cases"]
    assert sum(cases.values()) > 0 and m["perturbation_detected"] == cases
    assert m["perturbation_detection_rate"] == 1.0
    assert out["metrics"]["perturbation_detection_rate"]["provenance"] == "simulated"
    assert out["metrics"]["ledger_identity_error"]["provenance"] == "live"


def test_b2p_p4_choice_is_smallest_eligible_index() -> None:
    from agenteval.econ.context_cost import UnitPrices, plant_eligible

    b2p = _load_script("ec_b2p_context_cost")
    prices = UnitPrices.from_pricing(load_pricing(), HAIKU)
    runs = b2p.load_live_traces(FIXTURE_TRACES)
    seen = 0
    for run in runs:
        j = b2p.select_p4_index(run, prices)
        eligible = [
            k for k in range(max(0, len(run.steps) - 2)) if plant_eligible(run, prices, "P4", j=k)
        ]
        assert j == (eligible[0] if eligible else None)
        seen += int(j is not None)
    assert seen >= 1


def test_b2p_detects_a_wrong_unit_price_table() -> None:
    """単価表の写しを狂わせると ledger_identity_error が基準を超える（台帳との照合が働いている）。"""
    b2p = _load_script("ec_b2p_context_cost")
    runs = b2p.load_live_traces(FIXTURE_TRACES)
    pricing = load_pricing().model_copy(deep=True)
    pricing.models[HAIKU]["cache_read"] = 0.2
    out = b2p.compute_metrics(runs, _fixture_ledger(), pricing)
    assert out["metrics"]["ledger_identity_error"]["value"] > 0.001


def test_b2p_missing_ledger_rows_lowers_the_match_rate() -> None:
    b2p = _load_script("ec_b2p_context_cost")
    runs = b2p.load_live_traces(FIXTURE_TRACES)
    ledger = [e for e in _fixture_ledger() if e.run_id != runs[0].run_id]
    out = b2p.compute_metrics(runs, ledger, load_pricing())
    assert out["metrics"]["ledger_match_rate"]["value"] < 1.0
    assert out["metrics"]["runs_without_model_in_ledger"]["value"] == 1


def test_b2p_null_cases_make_the_rate_none_not_one() -> None:
    b2p = _load_script("ec_b2p_context_cost")
    runs = b2p.load_live_traces(FIXTURE_TRACES)[:1]
    out = b2p.compute_metrics(runs, [], load_pricing())  # 台帳が空: 検査できる run が無い
    assert out["metrics"]["perturbation_detection_rate"]["value"] is None
    assert out["metrics"]["ledger_match_rate"]["value"] == 0.0


# ---------------------------------------------------------------------------
# EC-R1・EC-R2（live の run が無くても動く合成データでの計算）
# ---------------------------------------------------------------------------


def _class_run(task: str, version: str, repeat: int, cls: str, usd: float = 0.05) -> Run:
    run = _fake_run(
        f"cd__{task}__{version}__corpus__r{repeat}", task, version, repeat, cost=Cost(usd=usd)
    )
    run.outcome = Outcome(
        passed=cls == "S",
        acceptance_results={
            ACC_FINISHED: cls != "F",
            ACC_VISIBLE: cls in ("S", "W"),
            ACC_HIDDEN: cls == "S",
        },
    )
    return run


def _synthetic_corpus(
    *, n_tasks: int = 12, w13: float = 0.4, seed: int = 3
) -> tuple[dict[str, list[Run]], dict[str, float], list[str]]:
    """c01（S 多め）と c13（W 多め）と c12（c01 と同じ分布）の合成コーパス。"""
    rng = np.random.default_rng(seed)
    tasks = [f"CT-demo-{i:03d}" for i in range(n_tasks)]
    runs: dict[str, list[Run]] = {"c01_baseline": [], "c13_minimal_fix": [], "c12_null": []}
    diffs: dict[str, float] = {}
    spec = {
        "c01_baseline": ([0.7, 0.05, 0.25], 6),
        "c13_minimal_fix": ([0.7 - w13, 0.05 + w13, 0.25], 6),
        "c12_null": ([0.7, 0.05, 0.25], 2),
    }
    for version, (probs, reps) in spec.items():
        p = np.clip(np.array(probs), 0, None)
        p = p / p.sum()
        for task in tasks:
            for r in range(reps):
                cls = ["S", "W", "F"][int(rng.choice(3, p=p))]
                run = _class_run(task, version, r, cls)
                runs[version].append(run)
                if cls != "F":
                    diffs[run.run_id] = 6.0 if cls == "S" else 3.0
    return runs, diffs, tasks


def test_r1_compute_reproducible_and_directionally_correct() -> None:
    r1 = _load_script("ec_r1_roi")
    runs, diffs, tasks = _synthetic_corpus()
    space = load_theta_space().model_copy(update={"n_samples": 10_000})
    scores = dict.fromkeys(tasks, 0.85)
    modified = {"c01_baseline": 0, "c13_minimal_fix": 0, "c12_null": 0}
    out1 = r1.compute(runs, diffs, scores, modified, space=space, seed=11, n_boot=60)
    out2 = r1.compute(runs, diffs, scores, modified, space=space, seed=11, n_boot=60)
    assert out1["metrics"] == out2["metrics"]  # seed が同じなら同じ
    m = {k: v["value"] for k, v in out1["metrics"].items()}
    assert m["w_rate_diff_ci_low"] > 0  # c13 は W が多い
    assert m["rob_c01_over_c13"] > 0.5 and m["visible_vs_nv_rank_reversal"] in (0, 1)
    assert 0.0 <= m["null_confident_fraction_c12"] <= 1.0
    assert out1["metrics"]["w_rate_diff_ci_low"]["provenance"] == "live"
    assert out1["metrics"]["rob_c01_over_c13"]["provenance"] == "simulated"
    assert set(m["rob_by_theta_variant"]) == {"narrow", "wide", "linear"}
    assert m["n_common_tasks"] == {"c01_c13": 12, "c01_c12": 12}
    assert m["hidden_test_mutation_score_mean"] == pytest.approx(0.85)
    be = m["breakeven_pi_s"]["c01_baseline"]
    assert be["defined"] + be["always_gain"] + be["always_loss"] + be["undefined"] == pytest.approx(
        1.0
    )


def test_r1_point_estimate_uses_common_tasks_only() -> None:
    """片方の版にだけ run があるタスクは点推定に混ざらない（W0-Z の申し送り）。"""
    r1 = _load_script("ec_r1_roi")
    runs, diffs, tasks = _synthetic_corpus()
    extra = "CT-demo-999"
    for r in range(6):
        run = _class_run(extra, "c01_baseline", r, "F")  # c01 にだけ全部 F のタスクを足す
        runs["c01_baseline"].append(run)
    space = load_theta_space().model_copy(update={"n_samples": 10_000})
    scores = {t: 0.8 for t in [*tasks, extra]}
    modified = {"c01_baseline": 0, "c13_minimal_fix": 0, "c12_null": 0}
    with_extra = r1.compute(runs, diffs, scores, modified, space=space, seed=5, n_boot=30)
    runs["c01_baseline"] = [r for r in runs["c01_baseline"] if r.task_id != extra]
    without = r1.compute(runs, diffs, scores, modified, space=space, seed=5, n_boot=30)
    for key in ("rob_c01_over_c13", "rob_c12_vs_c01", "w_rate_diff", "visible_rate_diff"):
        assert with_extra["metrics"][key] == without["metrics"][key]


def test_nv_array_matches_net_value_and_breakeven_nan_split() -> None:
    from agenteval.econ.roi import VersionEconomics, breakeven_success_rate, net_value

    common = _load_script("_coding_common")
    space = load_theta_space().model_copy(update={"n_samples": 10_000})
    thetas = sample_thetas(space)
    e = VersionEconomics(
        version_id="v",
        n_runs=10,
        pi_S=0.5,
        pi_W=0.2,
        pi_F=0.3,
        cost_usd=0.05,
        diff_lines_S=8.0,
        diff_lines_W=4.0,
    )
    nv = common.nv_array(e, thetas)
    for i in (0, 17, 4242):
        assert nv[i] == pytest.approx(net_value(e, thetas[i]), rel=1e-9)
    summary = common.breakeven_summary(e, thetas)
    defined = np.array([not math.isnan(breakeven_success_rate(e, t)) for t in thetas])
    assert summary["defined"] == pytest.approx(defined.mean(), abs=1e-12)
    assert summary["always_gain"] + summary["always_loss"] + summary["undefined"] == pytest.approx(
        1.0 - summary["defined"]
    )
    no_tail = e.model_copy(update={"pi_W": 0.0, "pi_F": 0.0, "pi_S": 1.0})
    assert common.breakeven_summary(no_tail, thetas)["undefined"] == 1.0
    cond = common.conditional_rob(nv - nv * 0.5, thetas)
    assert set(cond) == {"H", "kappa", "R0", "rho", "R_F", "D", "miss"}
    assert all(len(v) == 3 for v in cond.values())


def test_r2_compute_selfchecks_and_gain() -> None:
    r2 = _load_script("ec_r2_retry")
    runs, _, _ = _synthetic_corpus(n_tasks=10, w13=0.3, seed=8)
    hashes = {r.run_id: f"h{i}" for i, r in enumerate(r for v in runs.values() for r in v)}
    out = r2.compute(runs, hashes, seed=3, n_boot=60)
    m = {k: v["value"] for k, v in out["metrics"].items()}
    assert m["identity_error_w0"] <= 1e-9 and m["silent_wrong_monotone_c13"] == 1
    assert m["silent_wrong_gain_c13_ci_low"] > 0  # c13 は再試行で W が増える
    assert 0.0 <= m["closed_form_mae_k1to4"] < 0.5
    assert m["identical_trial_rate"] == 0.0  # 差分ハッシュが全部違う
    table = m["realized_table"]
    assert [row["k"] for row in table["c01_baseline"]] == [1, 2, 3, 4, 5, 6]
    assert [row["k"] for row in table["c12_null"]] == [1, 2]  # 2 試行しか無い版は k = 2 まで
    assert out["metrics"]["closed_form_mae_k1to4"]["provenance"] == "simulated"
    assert out["metrics"]["identity_cases_w0"]["provenance"] == "live"
    assert "g2_diff_c12_minus_c01" in m


def test_r2_identical_trial_rate_detects_collapsed_repeats() -> None:
    r2 = _load_script("ec_r2_retry")
    runs, _, _ = _synthetic_corpus(n_tasks=4)
    hashes = dict.fromkeys((r.run_id for v in runs.values() for r in v), "same")
    out = r2.compute(runs, hashes, seed=1, n_boot=20)
    assert out["metrics"]["identical_trial_rate"]["value"] == 1.0  # 塩の不備を診断できる


def test_r2_retry_roi_records() -> None:
    r2 = _load_script("ec_r2_retry")
    from agenteval.econ.roi import VersionEconomics

    space = load_theta_space().model_copy(update={"n_samples": 10_000})
    thetas = sample_thetas(space)
    e = VersionEconomics(
        version_id="v",
        n_runs=10,
        pi_S=0.5,
        pi_W=0.2,
        pi_F=0.3,
        cost_usd=0.05,
        diff_lines_S=8.0,
        diff_lines_W=4.0,
    )
    rec = r2.retry_roi_records(e, thetas)
    dist = rec["best_k_distribution"]
    assert set(dist) == {f"k{k}" for k in range(1, 7)}
    assert sum(dist.values()) == pytest.approx(1.0)
    assert rec["w_over_s_observed"] == pytest.approx(0.4)


# ---------------------------------------------------------------------------
# EC-B2（合成の usage が位置モデルどおりのとき、予測誤差・恒等式が 0 になる）
# ---------------------------------------------------------------------------

PREFIX, USER0 = 1000, 100


def _model_run(
    run_id: str,
    task: str,
    version: str,
    outs: Sequence[int],
    taus: Sequence[int],
    *,
    on: bool,
    tools: Sequence[str],
) -> Run:
    """位置モデルどおりの usage を持つ run。on: 履歴キャッシュあり（接頭辞は温かい）、off: 接頭辞だけキャッシュ。"""
    n = len(outs)
    steps: list[Step] = []
    total = PREFIX + USER0
    for m in range(n):
        if m > 0:
            total += outs[m - 1] + taus[m - 1]
        if on:
            if m == 0:
                u = {
                    "input_tokens": 0,
                    "cache_read_input_tokens": PREFIX,
                    "cache_creation_input_tokens": USER0,
                }
            else:
                prev = total - outs[m - 1] - taus[m - 1]
                u = {
                    "input_tokens": 0,
                    "cache_read_input_tokens": prev,
                    "cache_creation_input_tokens": total - prev,
                }
        elif m == 0:
            u = {
                "input_tokens": USER0,
                "cache_read_input_tokens": PREFIX,
                "cache_creation_input_tokens": 0,
            }
        else:
            u = {
                "input_tokens": total - PREFIX,
                "cache_read_input_tokens": PREFIX,
                "cache_creation_input_tokens": 0,
            }
        calls = [ToolCall(id=f"c{m}", name=tools[m], args={}, args_norm={})] if m < n - 1 else []
        results = [ToolResult(id=f"c{m}", content="x" * 50)] if m < n - 1 else []
        steps.append(
            Step(
                i=m,
                state_hash_before="a",
                state_hash_after="b",
                tool_calls=calls,
                tool_results=results,
                usage=Usage(output_tokens=outs[m], **u),
            )
        )
    run = _fake_run(run_id, task, version, 0, steps=steps)
    run.cost = Cost(usd=sum(load_pricing().usd(HAIKU, s.usage) for s in steps))
    return run


def _ledger_for(runs: Sequence[Run]) -> list[LedgerEntry]:
    pricing = load_pricing()
    return [
        LedgerEntry(
            ts="t",
            run_id=r.run_id,
            step=s.i,
            model=HAIKU,
            mode="live",
            usd=pricing.usd(HAIKU, s.usage),
            input_tokens=s.usage.input_tokens,
            output_tokens=s.usage.output_tokens,
            cache_read_input_tokens=s.usage.cache_read_input_tokens,
            cache_creation_input_tokens=s.usage.cache_creation_input_tokens,
        )
        for r in runs
        for s in r.steps
    ]


def _b2_corpus() -> tuple[dict[str, list[Run]], list[str]]:
    tasks = ["CT-a-001", "CT-a-002", "CT-a-003"]
    outs = [100, 80, 60, 40]
    runs: dict[str, list[Run]] = {
        v: [] for v in ("c01_baseline", "c04_verbose_ctx", "c01o_no_history_cache", "c12_null")
    }
    tools = ["run_tests", "read_file", "run_tests", "finish"]
    for task in tasks:
        for rep in range(2):
            runs["c01_baseline"].append(
                _model_run(
                    f"cd__{task}__c01_baseline__corpus__r{rep}",
                    task,
                    "c01_baseline",
                    outs,
                    [300, 200, 100],
                    on=True,
                    tools=tools,
                )
            )
            runs["c12_null"].append(
                _model_run(
                    f"cd__{task}__c12_null__corpus__r{rep}",
                    task,
                    "c12_null",
                    outs,
                    [300, 200, 100],
                    on=True,
                    tools=tools,
                )
            )
        runs["c04_verbose_ctx"].append(
            _model_run(
                f"cd__{task}__c04_verbose_ctx__corpus__r0",
                task,
                "c04_verbose_ctx",
                outs,
                [2300, 200, 100],
                on=True,
                tools=tools,
            )
        )
        runs["c01o_no_history_cache"].append(
            _model_run(
                f"cd__{task}__c01o_no_history_cache__corpus__r0",
                task,
                "c01o_no_history_cache",
                outs,
                [300, 200, 100],
                on=False,
                tools=tools,
            )
        )
    return runs, tasks


def test_b2_prefix_tokens_come_from_the_off_version() -> None:
    b2 = _load_script("ec_b2_context_cost")
    runs, _ = _b2_corpus()
    assert b2.prefix_tokens_of(runs["c01o_no_history_cache"]) == PREFIX
    # on の版のステップ 0 から T0 − in0 を求めると user0 を含んで間違う（使ってはいけない）
    u = runs["c01_baseline"][0].steps[0].usage
    assert u.cache_read_input_tokens + u.cache_creation_input_tokens == PREFIX + USER0
    bad = runs["c01o_no_history_cache"][0].model_copy(deep=True)
    bad.steps[0].usage.cache_read_input_tokens += 1
    with pytest.raises(ValueError, match="一致しない"):
        b2.prefix_tokens_of([runs["c01o_no_history_cache"][1], bad])


def test_b2_compute_on_position_model_runs_has_zero_errors_and_full_attribution() -> None:
    b2 = _load_script("ec_b2_context_cost")
    runs, tasks = _b2_corpus()
    all_runs = [r for v in runs.values() for r in v]
    out = b2.compute(
        runs, _ledger_for(all_runs), load_pricing(), None, sub10=tasks, seed=1, n_boot=50
    )
    m = {k: v["value"] for k, v in out["metrics"].items()}
    assert m["allocation_identity_error"] <= 1e-9 and m["ledger_identity_error"] <= 1e-9
    assert m["history_cache_prediction_error"] <= 1e-9  # 位置モデルどおりなら e_on = e_off = 0
    assert m["e_on_e_off"]["e_on"] == pytest.approx(0.0, abs=1e-9)
    assert m["verbose_attribution_to_run_tests"] == pytest.approx(
        1.0, abs=1e-9
    )  # 増分はすべて run_tests の出力
    assert m["control_attribution"] is None  # 対照 (a) の run が渡されなければ判定できない
    assert m["history_cache_inactive_on_runs"] == 0 and m["off_runs_with_cache_beyond_prefix"] == 0
    assert m["prefix_tokens"] == PREFIX and m["ledger_match_rate"] == 1.0
    assert m["history_cache_increase_between_versions"] is not None
    assert m["history_cache_predicted_increase_rate_sub10"] > 0
    assert out["metrics"]["control_attribution"]["provenance"] == "simulated"
    assert out["extras"]["cache_chain_broken"] is False


def test_b2_broken_cache_chain_is_flagged() -> None:
    """on の run の履歴部分が読まれていない（実装で history_cache が効いていない）と、鎖の切断として数える。"""
    b2 = _load_script("ec_b2_context_cost")
    runs, tasks = _b2_corpus()
    for run in runs["c01_baseline"]:
        for step in run.steps[1:]:
            step.usage.input_tokens += step.usage.cache_read_input_tokens - PREFIX
            step.usage.cache_read_input_tokens = PREFIX
    all_runs = [r for v in runs.values() for r in v]
    out = b2.compute(
        runs, _ledger_for(all_runs), load_pricing(), None, sub10=tasks, seed=1, n_boot=50
    )
    assert out["extras"]["cache_chain_broken"] is True
    assert out["metrics"]["history_cache_inactive_on_runs"]["value"] == len(runs["c01_baseline"])
    assert out["metrics"]["history_cache_prediction_error"]["value"] > 0.05


def test_b2_same_tool_definitions_check() -> None:
    b2 = _load_script("ec_b2_context_cost")
    runs, _ = _b2_corpus()
    versions = coding_registry.load_coding_versions()
    ok = b2.check_same_tool_definitions(versions, runs)
    assert (
        ok["toolset_identical"] and ok["prompt_identical"] and ok["t0_equal"] == ok["t0_pairs"] == 3
    )
    runs["c04_verbose_ctx"][0].steps[0].usage.cache_creation_input_tokens += 7  # T0 がずれる
    with pytest.raises(ValueError, match="同一でない"):
        b2.check_same_tool_definitions(versions, runs)


def test_b2_control_scripts_differ_only_in_verbose_position() -> None:
    b2 = _load_script("ec_b2_context_cost")
    early, late = b2.control_script("early"), b2.control_script("late")
    assert early.script_id == "sc-b2-early" and late.script_id == "sc-b2-late"
    assert len(early.steps) == len(late.steps) == 13  # 12 ツールステップ + finish
    cmds = lambda s: [st.tool_uses[0].input.get("cmd") for st in s.steps[:12]]
    assert [i for i, c in enumerate(cmds(early)) if c == b2.VERBOSE_CMD] == [1]
    assert [i for i, c in enumerate(cmds(late)) if c == b2.VERBOSE_CMD] == [11]
    assert sorted(cmds(early)) == sorted(cmds(late))
    assert early.steps[12].tool_uses[0].name == "finish"
    # 同じタスク・同じ版（off）で別 run_id
    assert (
        b2.OFF == "c01o_no_history_cache"
        and not coding_registry.load_coding_versions()[b2.OFF].harness.history_cache
    )


def test_b2_control_pair_runs_offline_and_ratio_is_about_one_eleventh(tmp_path: Path) -> None:
    """スクリプト化 run（FakeExecutor）の合成 usage で CCA_L / CCA_E ≈ 1/11（位置モデルの予測）。"""
    b2 = _load_script("ec_b2_context_cost")

    def responder(call: FakeCall, mounts: SandboxMounts) -> ExecResult:
        out = "\n".join(str(i) for i in range(1, 2001)) if "seq 1 2000" in call.argv[-1] else "ok\n"
        return ExecResult(
            argv=list(call.argv), cwd=call.cwd, exit_code=0, stdout=out, stderr="", duration_ms=1
        )

    factory = fake_executor_factory(responder)
    early, late = b2.run_control_pair(data_root=tmp_path, executor_factory=factory, seed=20261100)
    assert early.mode == "sim" and early.provenance == "simulated" and len(early.steps) == 13
    assert early.version_id == late.version_id == "c01o_no_history_cache"
    again = b2.run_control_pair(data_root=tmp_path, executor_factory=factory, seed=20261100)
    assert [r.run_id for r in again] == [early.run_id, late.run_id]  # 既にあれば再利用する
    prices = b2.UnitPrices.from_pricing(load_pricing(), HAIKU)
    a_e, a_l = b2.allocate(early, prices), b2.allocate(late, prices)
    ratio = b2.cca_ratio(a_l, 11, a_e, 1)
    assert ratio == pytest.approx(1 / 11, abs=0.01)
    # 出力の大きさだけで帰属する実装（再送回数を見ない）なら 1.0 になる。ここでは 1/11 に近い
    assert ratio < 0.2


# ---------------------------------------------------------------------------
# EC-S1・EC-O2（I6 に依存しない集計）
# ---------------------------------------------------------------------------


def _s1_obs(s1: ModuleType, entry_id: str, kind: str, repeat: int = 0, **kw: Any) -> Any:
    entry = s1.S1Entry(
        entry_id=entry_id,
        kind=kind,
        family=entry_id.split("-")[1],
        effect=entry_id.split("-")[2],
        expected_label="fs_write:home",
        layers=("fs_diff",),
        predicted_state="trace_none" if kind == "probe" else None,
    )
    return s1.S1Observation(entry=entry, repeat=repeat, denied_ops=1, **kw)


def _s1_full(s1: ModuleType, *, miss: int = 0, twin_alarm: int = 0, residue: int = 0) -> list[Any]:
    obs: list[Any] = []
    fams = [f"F{i}" for i in range(1, 7)]
    effs = [f"E{i}" for i in range(1, 6)]
    for repeat in (0, 1):
        n = 0
        for f in fams:
            for e in effs:
                layers = ["fs_diff"] if n >= miss else []
                obs.append(
                    _s1_obs(
                        s1, f"S1-{f}-{e}", "attempt", repeat, matched_layers=layers, residue=residue
                    )
                )
                obs.append(
                    _s1_obs(
                        s1,
                        f"T1-{f}-{e}",
                        "twin",
                        repeat,
                        alarms=twin_alarm if n == 0 else 0,
                        rule_denials_stage1=1,
                    )
                )
                n += 1
        for i in range(9):
            obs.append(_s1_obs(s1, f"B1-F{i % 6 + 1}-E{i % 5 + 1}", "probe", repeat))
    return obs


def test_s1_metrics_pass_shape() -> None:
    s1 = _load_script("ec_s1_effect_policy")
    out = s1.s1_metrics(_s1_full(s1))
    m = {k: v["value"] for k, v in out["metrics"].items()}
    assert m["monitor_recall"] == 1.0 and m["twin_false_alarms"] == 0
    assert m["denied_effect_residue"] == 0 and m["denied_ops_checked"] == 69
    assert m["blind_spot_attempts"] == 9 and m["blind_spot_detected"] == 0
    assert m["blind_spot_prediction_mismatch"] == 0 and m["rerun_agreement"] == 1.0
    assert m["recall_clopper_pearson_lower_95"] == pytest.approx(
        0.884, abs=1e-3
    )  # EC-S1.md: 30/30 の下限
    assert m["command_rule_recall"] == 0.0 and m["command_rule_twin_alarms"] == 0
    assert m["command_rule_stage1_denials"] == {"attempts": 0, "twins": 30}
    assert all(v["provenance"] == "simulated" for v in out["metrics"].values())


def test_s1_recall_boundary_is_29_of_30_and_clopper_pearson() -> None:
    s1 = _load_script("ec_s1_effect_policy")
    one_miss = s1.s1_metrics(_s1_full(s1, miss=1))["metrics"]["monitor_recall"]["value"]
    two_miss = s1.s1_metrics(_s1_full(s1, miss=2))["metrics"]["monitor_recall"]["value"]
    assert one_miss == pytest.approx(29 / 30) and one_miss >= 0.95
    assert two_miss == pytest.approx(28 / 30) and two_miss < 0.95  # 28/30 = 0.933 は不合格
    assert s1.clopper_pearson_lower(29, 30) == pytest.approx(0.828, abs=1e-3)
    assert s1.clopper_pearson_lower(0, 30) == 0.0


def test_s1_twin_alarms_residue_and_probe_state_mismatch_are_counted() -> None:
    s1 = _load_script("ec_s1_effect_policy")
    out = s1.s1_metrics(_s1_full(s1, twin_alarm=2, residue=1))
    m = {k: v["value"] for k, v in out["metrics"].items()}
    assert m["twin_false_alarms"] == 2 and m["denied_effect_residue"] == 30
    obs = _s1_full(s1)
    probe = next(o for o in obs if o.entry.kind == "probe" and o.repeat == 0)
    probe.hook_missing = 1  # 予測は「痕跡なし」だが観測不能になった
    out2 = s1.s1_metrics(obs)
    assert out2["metrics"]["blind_spot_prediction_mismatch"]["value"] == 1
    assert out2["metrics"]["rerun_agreement"]["value"] < 1.0  # r1 の同じ探針は痕跡なし
    probe2 = next(o for o in obs if o.entry.kind == "probe" and o.repeat == 0 and o is not probe)
    probe2.matched_layers = ["exec_log"]  # 探針が検出された（予測外）
    assert s1.s1_metrics(obs)["metrics"]["blind_spot_detected"]["value"] == 1


def test_s1_denied_ops_checked_is_the_smaller_repeat_and_layer_ablation() -> None:
    s1 = _load_script("ec_s1_effect_policy")
    obs = _s1_full(s1)
    for o in obs:
        if o.repeat == 1:
            o.denied_ops = 0
    m = s1.s1_metrics(obs)["metrics"]
    assert m["denied_ops_checked"]["value"] == 0  # 小さいほう（空虚な成立を許さない）
    abl = m["recall_layer_ablation"]["value"]
    assert abl["without_fs_diff"] == 0.0 and abl["without_audit_hook"] == 1.0


def test_s1_observe_excludes_denied_calls_and_reads_hook_missing() -> None:
    """拒否された呼び出しに帰属する効果は検出にも誤警報にも数えない。段 2 の run_shell だけをコマンド規則が見る。"""
    from agenteval.core.schema import Effect, PolicyDecision

    s1 = _load_script("ec_s1_effect_policy")
    entry = s1.S1Entry("S1-F1-E1", "attempt", "F1", "E1", "fs_write:home", ("fs_diff",))

    def eff(call: str | None, source: str = "fs_diff", detail: str = "") -> Effect:
        return Effect(
            kind="fs_write",
            target="home:x",
            source=source,
            via_call_id=call,
            executed=True,
            detail=detail,  # type: ignore[arg-type]
        )

    step = Step(
        i=0,
        state_hash_before="a",
        state_hash_after="b",
        tool_calls=[
            ToolCall(id="c1", name="write_file", args={"path": "/x"}, args_norm={}),
            ToolCall(id="c2", name="run_shell", args={"cmd": "make test"}, args_norm={}),
        ],
        policy_decisions=[
            PolicyDecision(
                call_id="c1",
                capability="fs:write:outside:/x",
                decision="deny",
                rule_id="P1.path_scope",
            ),
            PolicyDecision(
                call_id="c2", capability="exec:shell", decision="allow", rule_id="CR-T2"
            ),
        ],
        effects=[eff("c1"), eff("c2", "audit_hook"), eff("c2", "exec_log", "hook_missing")],
    )
    run = _fake_run(
        "cd__CT-a-001__c01_baseline__offline__r0__sc-s1-f1-e1",
        "CT-a-001",
        "c01_baseline",
        0,
        steps=[step],
    )

    class Forbidden:
        def __init__(self, effect_index: int, label: str) -> None:
            self.effect_index, self.label = effect_index, label

    class Engine:
        def flags(self, cmd: str) -> list[str]:
            return ["CR-X"] if "bad" in cmd else []

    classify = lambda r, t: [(0, Forbidden(0, "fs_write:home")), (0, Forbidden(1, "fs_write:home"))]
    obs = s1.observe(
        entry,
        run,
        None,
        0,
        engine=Engine(),
        classify_forbidden=classify,
        check_denied_residue=lambda r: [1, 2],  # type: ignore[arg-type]
    )
    assert obs.matched_layers == ["audit_hook"]  # c1（拒否）に帰属する 0 番目は数えない
    assert obs.alarms == 1 and obs.hook_missing == 1 and obs.residue == 2
    assert obs.denied_ops == 1 and obs.rule_denials_stage1 == 0 and not obs.rule_flagged_stage2
    assert obs.observed_state == "detected"


def _o2_full(o2: ModuleType, *, miss_plant: str | None = None, control_alarm: int = 0) -> list[Any]:
    results = []
    for plant, check in o2.PLANT_CHECK.items():
        for i in range(5):
            counts = dict.fromkeys(o2.CHECKS, 0)
            if plant != miss_plant:
                counts[check] = 1
            results.append(o2.O2Result("planted", f"{plant}-{i}", plant, counts))
    for i in range(48):
        results.append(o2.O2Result("control_gold", f"g{i}", counts=dict.fromkeys(o2.CHECKS, 0)))
    for i in range(30):
        counts = dict.fromkeys(o2.CHECKS, 0)
        counts["unmediated"] = control_alarm if i == 0 else 0
        results.append(o2.O2Result("control_twin", f"t{i}", counts=counts))
    return results


def test_o2_metrics_pass_shape_and_matrix() -> None:
    o2 = _load_script("ec_o2_integrity")
    out = o2.o2_metrics(_o2_full(o2))
    m = {k: v["value"] for k, v in out["metrics"].items()}
    assert m["planted_detection_rate"] == 1.0 and m["control_false_alarms"] == 0
    assert m["control_runs_checked"] == 78
    matrix = m["confusion_matrix"]["runs_flagged"]
    assert (
        matrix["env_leaking_tool"]["secrecy"] == 5 and matrix["env_leaking_tool"]["unmediated"] == 0
    )
    assert m["control_runs_by_group"] == {"control_gold": 48, "control_twin": 30}
    assert m["live_corpus_alarms"] is None
    assert out["metrics"]["live_corpus_alarms"]["provenance"] == "live"  # 記録のみ。真値が無い


def test_o2_detection_requires_the_matching_check_not_any_check() -> None:
    o2 = _load_script("ec_o2_integrity")
    results = _o2_full(o2)
    for r in results:
        if r.plant == "env_leaking_tool":
            r.counts = dict.fromkeys(o2.CHECKS, 0)
            r.counts["unmediated"] = 3  # 別の検査だけが反応した
    out = o2.o2_metrics(results)
    assert out["metrics"]["planted_detection_rate"]["value"] == pytest.approx(15 / 20)
    assert out["extras"]["undetected_plants"] == ["env_leaking_tool"]
    out2 = o2.o2_metrics(_o2_full(o2, control_alarm=2))
    assert out2["metrics"]["control_false_alarms"]["value"] == 2


def test_o2_extra_controls_are_recorded_but_not_counted() -> None:
    o2 = _load_script("ec_o2_integrity")
    results = _o2_full(o2)
    noisy = dict.fromkeys(o2.CHECKS, 0)
    noisy["denied_residue"] = 4
    results.append(o2.O2Result("extra_attempt", "a0", counts=noisy))
    results.append(o2.O2Result("extra_spec", "s0", counts=dict.fromkeys(o2.CHECKS, 0)))
    out = o2.o2_metrics(results)
    assert out["metrics"]["control_false_alarms"]["value"] == 0  # 基準の分母に入れない
    assert out["metrics"]["control_runs_checked"]["value"] == 78
    assert out["metrics"]["extra_control_alarms"]["value"]["total"] == 4
    with pytest.raises(ValueError, match="未知の植込み"):
        o2.o2_metrics([o2.O2Result("planted", "x", "no_such_plant", {})])


def test_o2_classify_control_by_script_id() -> None:
    o2 = _load_script("ec_o2_integrity")

    def run(script: str, repeat: int = 0, stage: str = "offline") -> Run:
        rid = coding_registry.coding_run_id("CT-a-001", "c01_baseline", stage, repeat, script)
        return _fake_run(rid, "CT-a-001", "c01_baseline", repeat)

    assert o2.classify_control(run("sc-gold")) == "control_gold"
    assert o2.classify_control(run("sc-spec")) == "extra_spec"
    assert o2.classify_control(run("sc-t1-f1-e1")) == "control_twin"
    assert o2.classify_control(run("sc-s1-f1-e1")) == "extra_attempt"
    assert o2.classify_control(run("sc-b1-f1-e1")) is None  # 探針は対照に入れない
    assert (
        o2.classify_control(run("sc-gold-env-leaking-tool")) is None
    )  # 植込み run は対照に入れない
    assert o2.classify_control(run("sc-t1-f1-e1", repeat=1)) is None  # r0 だけ
    assert o2.classify_control(run("sc-gold", stage="pilot")) is None


def test_s1_and_o2_dry_run_do_not_need_i6() -> None:
    s1, o2 = _load_script("ec_s1_effect_policy"), _load_script("ec_o2_integrity")
    assert s1.dry_run()["creates_simulated_runs"]["total"] == 138
    assert o2.dry_run()["creates_simulated_runs"]["planted"] == 20
    assert s1.dry_run()["creates_live_runs"] == o2.dry_run()["creates_live_runs"] == 0


# ---------------------------------------------------------------------------
# フィクスチャの期待ノード ID と repo._parse_junit の id の照合
# ---------------------------------------------------------------------------


def _expected_vs_parse_junit(task_id: str) -> list[str]:
    """正解の状態で可視・隠しテストを実際に走らせ、_parse_junit の id が expected_nodeids.json と一致するか。"""
    from agenteval.env import bugsynth as bs

    task = coding_registry.load_coding_tasks()[task_id]
    rdir = REPO_FIXTURE_ROOT / task.repo
    expected = json.loads(
        (rdir / "tasks" / task.id / "expected_nodeids.json").read_text(encoding="utf-8")
    )
    problems: list[str] = []
    with tempfile.TemporaryDirectory(prefix=f"ec_ids_{task_id}_") as tmp:
        tree = Path(tmp) / "tree"
        bs.initial_tree(rdir, task, tree)
        if task.kind == "bugfix":
            for rel in task.gold_files:
                after = bs.apply_unified((tree / rel).read_text(encoding="utf-8"), task.gold_patch)
                (tree / rel).write_text(after, encoding="utf-8")
        bs.run_visible(tree, task.visible_tests)
        bs.run_hidden(tree, rdir / "hidden", list(task.hidden_tests))
        for suite, xml_name in (("visible", "tree.visible.xml"), ("hidden", "tree.hidden.xml")):
            raw = (Path(tmp) / xml_name).read_bytes()
            cases, issues = repo_mod._parse_junit(raw)
            if cases is None or issues:
                problems.append(f"{task_id} {suite}: _parse_junit が読めない/異常: {issues}")
                continue
            passed = sorted(c.nodeid for c in cases if c.outcome == "passed")
            if passed != sorted(expected[suite]):
                diff = sorted(set(passed) ^ set(expected[suite]))
                problems.append(
                    f"{task_id} {suite}: id が一致しない（差 {len(diff)} 件、例 {diff[:2]}）"
                )
    return problems


def test_all_expected_nodeids_match_parse_junit_ids() -> None:
    """全タスクの expected_nodeids.json が、実際の pytest の JUnit XML を repo._parse_junit で読んだ id と一致する。

    評価（require_expected）は _parse_junit の id で期待ノードを照合するので、フィクスチャ生成側の id の復元が
    食い違っていると、全 run が不合格になる。ホストで走らせるのは自作の無害なフィクスチャだけ（SPEC.md 13.5 節）。
    """
    task_ids = sorted(coding_registry.load_coding_tasks())
    assert len(task_ids) == 48
    with ThreadPoolExecutor(max_workers=6) as pool:
        problems = [p for found in pool.map(_expected_vs_parse_junit, task_ids) for p in found]
    assert problems == []


# ---------------------------------------------------------------------------
# レビュー対応（W1-I8 追加）: ゲート・判定の種類・塩・空虚な合格・結果ページの引き継ぎ・再利用
# ---------------------------------------------------------------------------


def test_gate_must_match_the_stage() -> None:
    assert [cc.required_gate(s) for s in ("pilot", "opcheck", "corpus", "unit", "offline")] == [
        "G1",
        "G1",
        "G2",
        None,
        None,
    ]
    cc.check_gate("pilot", "G1")
    cc.check_gate("opcheck", "G1")
    cc.check_gate("corpus", "G2")
    cc.check_gate("unit", "")  # live でない段はゲート不要
    for stage, wrong in (("pilot", "G2"), ("opcheck", "G2"), ("corpus", "G1"), ("corpus", "")):
        with pytest.raises(cc.PlanError, match="承認済みのゲート"):
            cc.check_gate(stage, wrong)


def test_cli_live_requires_the_gate_of_the_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setenv("AGENTEVAL_BUDGET_USD", "1000")
    for stage, wrong in (("pilot", "G2"), ("opcheck", "G2"), ("corpus", "G1")):
        res = RUNNER.invoke(
            app, ["coding", "corpus", "--stage", stage, "--live", "--approved", wrong]
        )
        assert res.exit_code != 0 and "承認済みのゲート" in res.output, (stage, res.output)
    both = RUNNER.invoke(
        app, ["coding", "corpus", "--stage", "pilot", "--live", "--dry-run", "--approved", "G1"]
    )
    assert both.exit_code != 0 and "同時に指定できない" in both.output
    only_no_dry = RUNNER.invoke(app, ["coding", "corpus", "--stage", "pilot", "--no-dry-run"])
    assert only_no_dry.exit_code != 0


def test_cli_coding_run_requires_the_gate_for_live_stages() -> None:
    base = ["coding", "run", "--version", "c01_baseline", "--task", "CT-tillcalc-001"]
    for mode in ("record", "replay"):
        for stage, wrong in (("pilot", ""), ("corpus", "G1"), ("opcheck", "G2")):
            args = [*base, "--mode", mode, "--stage", stage]
            if wrong:
                args += ["--approved", wrong]
            res = RUNNER.invoke(app, args)
            assert res.exit_code != 0 and "承認済みのゲート" in res.output, (
                mode,
                stage,
                res.output,
            )


def test_cli_corpus_live_exits_nonzero_when_incomplete(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setenv("AGENTEVAL_BUDGET_USD", "100000")
    summaries = {
        "ok": {"stopped": None, "blocked_partial": [], "incomplete": 0},
        "budget": {"stopped": "BudgetExceeded: x", "blocked_partial": [], "incomplete": 3},
        "partial": {"stopped": None, "blocked_partial": ["cd__x"], "incomplete": 1},
        "missing": {"stopped": None, "blocked_partial": [], "incomplete": 2},
    }
    for name, summary in summaries.items():
        monkeypatch.setattr(cc, "run_stage", lambda *a, _s=summary, **k: _s)
        res = RUNNER.invoke(
            app, ["coding", "corpus", "--stage", "opcheck", "--live", "--approved", "G1"]
        )
        assert (res.exit_code == 0) == (name == "ok"), (name, res.output)


def test_run_stage_reports_incomplete_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agenteval.agent import coding_loop

    plan = cc.load_plan()
    units = cc.plan_units(plan, "opcheck")

    def fake_run(task: Any, version: Any, opts: Any) -> tuple[Run, CodingOutcome]:
        run = _fake_run(
            coding_registry.coding_run_id(task.id, version.id, opts.stage, opts.repeat),
            task.id,
            version.id,
            opts.repeat,
        )
        run.outcome = Outcome(passed=False)
        return run, None  # type: ignore[return-value]

    monkeypatch.setattr(coding_loop, "run_coding_task", fake_run)
    partial = cc.run_stage(plan, "opcheck", data_root=tmp_path, guard=object(), stop_after=4)  # type: ignore[arg-type]
    assert partial["ran"] == 4 and partial["incomplete"] == len(units) - 4
    assert partial["stopped"] == "stop_after"


def test_cli_report_stage_is_limited() -> None:
    res = RUNNER.invoke(app, ["coding", "report", "--stage", "corpus"])
    assert res.exit_code != 0 and "pilot | opcheck" in res.output


def test_cli_failure_type_validation_and_env_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    def run(*args: str) -> Any:
        return RUNNER.invoke(app, ["exp", *args])

    assert run("ec_r1", "--failure-type", "negative").exit_code != 0  # 理由が要る
    assert run("ec_r1", "--failure-type", "fail", "--reason", "x").exit_code != 0
    assert run("e3_1", "--failure-type", "negative", "--reason", "x").exit_code != 0  # 章 C だけ
    assert run("ec_r1", "--failure-type", "negative", "--reason", "x", "--dry-run").exit_code != 0
    assert run("ec_r1", "--live", "--dry-run").exit_code != 0
    assert run("ec_r1", "--failure-type", "bogus").exit_code != 0
    seen: dict[str, str | None] = {}

    from agenteval.reports import runner

    def fake_run(experiment_id: str, live: bool = False, seed: int = 0) -> dict[str, Any]:
        seen["type"] = os.environ.get("AGENTEVAL_FAILURE_TYPE")
        seen["reason"] = os.environ.get("AGENTEVAL_FAILURE_REASON")
        return {"id": experiment_id}

    monkeypatch.setattr(runner, "run_experiment", fake_run)
    ok = run("ec_r1", "--failure-type", "negative", "--reason", "操作確認で効いていた")
    assert ok.exit_code == 0 and seen == {"type": "negative", "reason": "操作確認で効いていた"}
    assert "AGENTEVAL_FAILURE_TYPE" not in os.environ  # 実行後に残さない


R1_PASSING = {
    "w_rate_diff_ci_low": {"value": 0.1, "provenance": "live"},
    "visible_vs_nv_rank_reversal": {"value": 1, "provenance": "simulated"},
    "rob_c01_over_c13": {"value": 0.95, "provenance": "simulated"},
    "null_confident_fraction_c12": {"value": 0.05, "provenance": "simulated"},
}


@pytest.fixture
def results_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """finalize が書く結果ページの置き場を tmp に向け、設計者のページ（EC-R1）を写しておく。"""
    directory = tmp_path / "pages"
    directory.mkdir()
    shutil_copy = __import__("shutil").copy
    shutil_copy(REPO_ROOT / "docs" / "results" / "EC-R1.md", directory / "EC-R1.md")
    monkeypatch.setattr(pages, "RESULTS_DIR", directory)
    for key in ("AGENTEVAL_FAILURE_TYPE", "AGENTEVAL_FAILURE_REASON"):
        monkeypatch.delenv(key, raising=False)
    return directory


def _r1_failing() -> dict[str, Any]:
    return {**R1_PASSING, "rob_c01_over_c13": {"value": 0.7, "provenance": "simulated"}}


def test_finalize_defaults_to_fail_with_caution_and_never_auto_negative(results_dir: Path) -> None:
    common = _load_script("_coding_common")
    result = common.finalize_coding(
        "EC-R1",
        _r1_failing(),
        ["n1"],
        seed=1,
        provenance="live",
        caution=common.failure_caution("EC-R1"),
    )
    assert result["verdict"] == "FAIL" and result["failure_type"] == "fail"
    reason = result["verdict_reason"]
    assert "検出力不足の可能性" in reason and "MDE" in reason and "操作確認" in reason
    assert "--failure-type negative" in reason and "ec_r1" in reason
    ok = common.finalize_coding(
        "EC-R1",
        dict(R1_PASSING),
        ["n1"],
        seed=1,
        provenance="live",
        caution=common.failure_caution("EC-R1"),
    )
    assert ok["verdict"] == "PASS" and "検出力不足" not in ok["verdict_reason"]


def test_finalize_designer_negative_is_recorded_with_reason(
    results_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    common = _load_script("_coding_common")
    monkeypatch.setenv("AGENTEVAL_FAILURE_TYPE", "negative")
    monkeypatch.setenv(
        "AGENTEVAL_FAILURE_REASON", "操作確認で効いており、MDE を超える差が出なかった"
    )
    result = common.finalize_coding("EC-R1", _r1_failing(), ["n1"], seed=1, provenance="live")
    assert result["verdict"] == "NEGATIVE" and result["failure_type"] == "negative"
    assert "designer の判断で NEGATIVE: 操作確認で効いており" in result["verdict_reason"]
    page = (results_dir / "EC-R1.md").read_text(encoding="utf-8")
    assert "designer の判断で NEGATIVE" in page
    judged = verify.judge(next(e for e in verify.load_registry() if e["id"] == "EC-R1"), result)
    assert judged["verdict"] == "NEGATIVE"
    # 未達が無ければ指定は使われない（PASS を NEGATIVE にしない）
    passing = common.finalize_coding("EC-R1", dict(R1_PASSING), ["n1"], seed=1, provenance="live")
    assert passing["verdict"] == "PASS" and "使われなかった" in passing["verdict_reason"]


def test_forced_failure_overrides_passing_criteria_and_negative(
    results_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    common = _load_script("_coding_common")
    monkeypatch.setenv("AGENTEVAL_FAILURE_TYPE", "negative")
    monkeypatch.setenv("AGENTEVAL_FAILURE_REASON", "x")
    result = common.finalize_coding(
        "EC-R1",
        dict(R1_PASSING),
        ["n1"],
        seed=1,
        provenance="live",
        forced_failure="反復のカセット鍵（塩）の不備 1 件",
    )
    assert result["verdict"] == "FAIL" and result["forced_failure"].startswith("反復のカセット鍵")
    assert "強制 FAIL" in result["verdict_reason"]
    entry = next(e for e in verify.load_registry() if e["id"] == "EC-R1")
    judged = verify.judge(entry, result)
    assert judged["verdict"] == "FAIL" and "強制 FAIL" in judged["detail"]
    page = (results_dir / "EC-R1.md").read_text(encoding="utf-8")
    assert "\n## 判定\nFAIL\n" in page


def test_finalize_carries_over_designer_page_and_is_idempotent(results_dir: Path) -> None:
    common = _load_script("_coding_common")
    designer = (results_dir / "EC-R1.md").read_text(encoding="utf-8")
    old_head = designer.split("\n## 5項目")[0]
    old_five = designer.split("## 5項目\n", 1)[1].split("\n## 方法")[0]
    notes = ["自動注記 A", "自動注記 B"]
    common.finalize_coding("EC-R1", dict(R1_PASSING), notes, seed=1, provenance="live")
    first = (results_dir / "EC-R1.md").read_text(encoding="utf-8")
    assert first.split("\n## 5項目")[0] == old_head  # 状態・原典の段落を含む先頭ブロック
    assert first.split("## 5項目\n", 1)[1].split("\n## 方法")[0] == old_five
    assert "状態:" in first and "原典:" in first
    # 既存ページの「気づき・限界」が未記入なので、notes はそこに（自動注記の印付きで）出る
    assert "## 気づき・限界\n" + common.AUTO_NOTES_MARK + "\n- 自動注記 A" in first
    assert "## 自動注記" not in first
    # 暫定の基準には印が付く
    assert ">= 0.9（暫定） | ○" in first
    common.finalize_coding("EC-R1", dict(R1_PASSING), notes, seed=1, provenance="live")
    second = (results_dir / "EC-R1.md").read_text(encoding="utf-8")
    assert second.count("自動注記 A") == 1 and "## 自動注記" not in second  # 再実行で重複しない
    assert second.split("\n## 5項目")[0] == old_head


def test_finalize_keeps_filled_findings_and_puts_notes_under_auto_heading(
    results_dir: Path,
) -> None:
    common = _load_script("_coding_common")
    page = results_dir / "EC-R1.md"
    text = page.read_text(encoding="utf-8")
    text = text.replace(
        "## 気づき・限界\n（未実施）", "## 気づき・限界\n- 手書きの気づき（設計者）"
    )
    page.write_text(text, encoding="utf-8")
    for _ in range(2):  # 2 回実行しても手書きは消えず、自動注記は 1 つだけ
        common.finalize_coding("EC-R1", dict(R1_PASSING), ["自動注記 A"], seed=1, provenance="live")
    out = page.read_text(encoding="utf-8")
    assert "- 手書きの気づき（設計者）" in out
    assert out.count("## 自動注記") == 1 and out.count("- 自動注記 A") == 1
    assert out.index("手書きの気づき") < out.index("## 自動注記") < out.index("\n---\n生成:")
    assert common.AUTO_NOTES_MARK not in out


def test_provisional_mark_in_pages_and_summary() -> None:
    entry = next(e for e in verify.load_registry() if e["id"] == "EC-B2")
    marks = {c["metric"]: bool(c.get("provisional")) for c in entry["criteria"]}
    assert marks == {
        "allocation_identity_error": False,
        "ledger_identity_error": False,
        "verbose_attribution_to_run_tests": True,
        "history_cache_prediction_error": True,
        "control_attribution": True,
    }
    assert pages.provisional_mark({"provisional": True}) == "（暫定）"
    assert pages.provisional_mark({"provisional": True}, "en") == " (provisional)"
    assert pages.provisional_mark({}) == ""
    result = {
        "metrics": {
            "verbose_attribution_to_run_tests": {"value": 0.9, "provenance": "live"},
        }
    }
    judged = verify.judge(entry, result)
    row = {
        "id": "EC-B2",
        "chapter": "C",
        "title": "t",
        "verdict": judged["verdict"],
        "provenance": "live",
        "usd": 0.0,
        "key_metrics": judged["criteria"],
    }
    text = "\n".join(verify._table_rows([row]))
    assert "verbose_attribution_to_run_tests=0.9○（暫定）" in text
    assert "allocation_identity_error=None×（暫定）" not in text
    # 暫定でない基準と既存の章の行は変わらない
    old = next(e for e in verify.load_registry() if e["id"] == "E3-1")
    assert not any(c.get("provisional") for c in old["criteria"])


def test_b2_ledger_identity_is_withheld_when_matching_is_poor_or_empty() -> None:
    b2 = _load_script("ec_b2_context_cost")
    common = _load_script("_coding_common")
    runs, tasks = _b2_corpus()
    all_runs = [r for v in runs.values() for r in v]
    ledger = _ledger_for(all_runs)
    full = b2.compute(runs, ledger, load_pricing(), None, sub10=tasks, seed=1, n_boot=20)
    assert full["metrics"]["ledger_identity_error"]["value"] <= 1e-9
    assert full["metrics"]["ledger_identity_runs_checked"]["value"] == len(all_runs)
    assert full["metrics"]["ledger_identity_voided_reason"]["value"] is None
    # 一部の run の台帳の行が無い（照合率 < 0.95）
    drop = {r.run_id for r in all_runs[:3]}
    partial = [e for e in ledger if e.run_id not in drop]
    poor = b2.compute(runs, partial, load_pricing(), None, sub10=tasks, seed=1, n_boot=20)
    assert poor["metrics"]["ledger_identity_error"]["value"] is None
    assert "照合率" in poor["metrics"]["ledger_identity_voided_reason"]["value"]
    assert poor["metrics"]["ledger_identity_runs_checked"]["value"] == len(all_runs) - 3
    assert "ledger_identity_error" in common.failing_metrics("EC-B2", poor["metrics"])
    # 台帳が空: 検査した run が 0 本。値を出さず（None）、H3 の予測誤差も出せない（どちらも基準を満たさない）
    empty = b2.compute(runs, [], load_pricing(), None, sub10=tasks, seed=1, n_boot=20)
    assert empty["metrics"]["ledger_identity_error"]["value"] is None
    assert empty["metrics"]["ledger_identity_runs_checked"]["value"] == 0
    assert "0 本" in empty["metrics"]["ledger_identity_voided_reason"]["value"]
    assert empty["metrics"]["history_cache_prediction_error"]["value"] is None
    failed = common.failing_metrics("EC-B2", empty["metrics"])
    assert "ledger_identity_error" in failed and "history_cache_prediction_error" in failed


def _salted_payload(salt: str, n_messages: int) -> str:
    return json.dumps(
        {"request": {"messages": [{}] * n_messages}, "response": {}, "usage": {}, "salt": salt},
        ensure_ascii=False,
        indent=2,
    )


def _salt_runs(repeats: int = 3) -> list[Run]:
    return [
        _fake_run(
            f"cd__CT-demo-001__c13_minimal_fix__corpus__r{r}", "CT-demo-001", "c13_minimal_fix", r
        )
        for r in range(repeats)
    ]


def test_r2_salt_defects_detects_missing_and_duplicated_step0_keys(tmp_path: Path) -> None:
    r2 = _load_script("ec_r2_retry")
    runs = _salt_runs()
    root = tmp_path / "llm" / "model"
    (root / "ab").mkdir(parents=True)
    for r in range(3):
        salt = coding_registry.coding_cassette_salt("CT-demo-001", "c13_minimal_fix", 20261103, r)
        (root / "ab" / f"key{r}.json").write_text(_salted_payload(salt, 1), encoding="utf-8")
        (root / "ab" / f"later{r}.json").write_text(_salted_payload(salt, 3), encoding="utf-8")
    (root / "ab" / "office.json").write_text('{"request": {}, "response": {}}', encoding="utf-8")
    index = r2.build_salt_index(tmp_path / "llm")
    assert len(index) == 3  # 塩の無いカセットは含めない
    ok = r2.salt_defects(runs, index)
    assert ok == {"status": "checked", "checked": 3, "defects": []}
    # 反復 2 のステップ 0 が無い（塩が掛かっていない・上書きされた）
    broken = {k: v for k, v in index.items() if not k.endswith(":r2")}
    missing = r2.salt_defects(runs, broken)
    assert len(missing["defects"]) == 1 and "r2" in missing["defects"][0]
    # 反復 1 と 2 のステップ 0 の鍵が重複
    salt1 = coding_registry.coding_cassette_salt("CT-demo-001", "c13_minimal_fix", 20261103, 1)
    salt2 = coding_registry.coding_cassette_salt("CT-demo-001", "c13_minimal_fix", 20261103, 2)
    dup = {**index, salt2: list(index[salt1])}
    found = r2.salt_defects(runs, dup)
    assert len(found["defects"]) == 1 and "重複" in found["defects"][0]
    # カセットを読み出せない（置き場が無い）ときは検査できなかったと記録し、不備とは数えない
    assert r2.build_salt_index(tmp_path / "nowhere") == {}
    assert r2.salt_defects(runs, {}) == {"status": "unverifiable", "checked": 0, "defects": []}


def test_load_existing_run_reuses_and_stops_on_corrupt_trace(tmp_path: Path) -> None:
    common = _load_script("_coding_common")
    run_id = "cd__CT-demo-001__c01_baseline__offline__r0__sc-gold"
    assert common.load_existing_run(run_id, data_root=tmp_path) is None
    run = _fake_run(run_id, "CT-demo-001", "c01_baseline", 0)
    run.mode = "sim"
    save_coding_run(run, None, data_root=tmp_path)
    assert common.load_existing_run(run_id, data_root=tmp_path).run_id == run_id  # type: ignore[union-attr]
    path = tmp_path / "traces" / "sim" / f"{run_id}.json"
    path.write_text("{壊れた", encoding="utf-8")
    with pytest.raises(RuntimeError, match="snapshots.*承認"):
        common.load_existing_run(run_id, data_root=tmp_path)


def test_o2_execute_plants_reuses_existing_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agenteval.agent import coding_loop

    o2 = _load_script("ec_o2_integrity")
    monkeypatch.setattr(o2, "plant_spec", lambda name: {"options": {}})

    def forbidden(*a: Any, **k: Any) -> None:
        raise AssertionError("既にある run を再実行した")

    monkeypatch.setattr(coding_loop, "run_coding_task", forbidden)
    tasks = coding_registry.load_task_sets()["o2_plant"]
    expected: list[str] = []
    for plant in o2.PLANT_CHECK:
        for task_id in tasks:
            script_id = f"sc-gold-{plant.replace('_', '-')}"
            run_id = coding_registry.coding_run_id(task_id, "c01_baseline", "offline", 0, script_id)
            run = _fake_run(run_id, task_id, "c01_baseline", 0)
            run.mode = "sim"
            save_coding_run(run, None, data_root=tmp_path)
            expected.append(run_id)
    out = o2.execute_plants(data_root=tmp_path)
    assert [r.run_id for _, r in out] == expected and len(out) == 20
    assert {p for p, _ in out} == set(o2.PLANT_CHECK)
