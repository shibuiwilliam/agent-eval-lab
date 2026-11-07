"""EC-O2 の植込み 4 種のテスト（SPEC.md 12.4.9 節、CONTENT.md 11 章）。

Executor は FakeExecutor だけを使う（コードを実行しない）。植込みはハーネスの欠陥の模擬で、サンドボックスの中で
禁止効果を起こさない。各植込みについて、対応する I6a の検査が 1 件以上の警報を出し、同じタスクの `gold_script` の run
（植込み無し）には 4 検査とも 0 件であることを確かめる。live API は呼ばない（ScriptedAgent）。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

import pytest

from agenteval.agent.coding_loop import CodingRunOptions, run_coding_task
from agenteval.agent.scripted import gold_script
from agenteval.core.coding_registry import get_coding_task, get_coding_version
from agenteval.core.coding_types import (
    SCRIPT_ID_PATTERN,
    CodingTask,
    CodingVersion,
    SnapshotManifest,
)
from agenteval.core.schema import Run
from agenteval.env.repo_tools import build_toolset
from agenteval.env.sandbox import fake_executor_factory
from agenteval.obs import plants
from agenteval.obs.integrity import (
    IntegrityFinding,
    check_denied_residue,
    check_record_integrity,
    check_secrecy,
    check_unmediated,
)
from agenteval.obs.plants import (
    GOLD_WRITE_STEP,
    PLANT_CHECK,
    PLANT_NAMES,
    O2Plant,
    PlantName,
    PreAdr026Injector,
    o2_plant,
)
from agenteval.security.canary import CanaryVault

KEY = b"\x07" * 32
ALL_CHECKS = ("unmediated", "secrecy", "record_integrity", "denied_residue")


@pytest.fixture(scope="module")
def task() -> CodingTask:
    return get_coding_task("CT-tillcalc-002")


@pytest.fixture(scope="module")
def version() -> CodingVersion:
    return get_coding_version("c01_baseline")


def _run(
    tmp_path: Path, task: CodingTask, version: CodingVersion, plant: O2Plant | None
) -> tuple[Run, CanaryVault]:
    """正解パッチの台本（植込みがあれば 1 か所だけ差し込む）を FakeExecutor で走らせる。"""
    vault = CanaryVault(key=KEY)
    options: dict[str, Any] = {}
    script = gold_script(task)
    if plant is not None:
        options = plant.run_options()
        script = plant.script(task)
    run, _ = run_coding_task(
        task,
        version,
        CodingRunOptions(
            mode="sim",
            stage="offline",
            repeat=0,
            seed=1,
            script=script,
            executor_factory=fake_executor_factory(),
            vault=vault,
            data_root=tmp_path,
            **options,
        ),
    )
    return run, vault


def _load_manifest(ref: str) -> SnapshotManifest | None:
    path = Path(ref)
    if not path.is_file():
        return None
    return SnapshotManifest.model_validate_json(path.read_text(encoding="utf-8"))


def _findings(run: Run, vault: CanaryVault) -> dict[str, list[IntegrityFinding]]:
    """4 検査の結果（検査名 → 警報）。秘匿は保存した trace 全文を指紋で走査する。"""
    texts = {"trace.json": run.model_dump_json()}
    return {
        "unmediated": check_unmediated(run),
        "secrecy": check_secrecy(texts, vault.fingerprints(run.run_id), run),
        "record_integrity": check_record_integrity(run, _load_manifest),
        "denied_residue": check_denied_residue(run),
    }


# --- 公開情報 -----------------------------------------------------------------------------
def test_plant_names_checks_and_scripts(task: CodingTask) -> None:
    assert PLANT_NAMES == (
        "unmediated_write_tool",
        "env_leaking_tool",
        "silent_snapshot_hook",
        "pre_adr026_injector",
    )
    assert dict(PLANT_CHECK) == {
        "unmediated_write_tool": "unmediated",
        "env_leaking_tool": "secrecy",
        "silent_snapshot_hook": "record_integrity",
        "pre_adr026_injector": "denied_residue",
    }
    assert GOLD_WRITE_STEP == 1
    gold = gold_script(task)
    assert [t.name for s in gold.steps for t in s.tool_uses][:4] == [
        "read_file",
        "write_file",
        "run_tests",
        "finish",
    ]
    for name in PLANT_NAMES:
        plant = o2_plant(name)
        assert (plant.name, plant.check) == (name, PLANT_CHECK[name])
        script = plant.script(task)
        assert script.script_id == f"sc-gold-{name.replace('_', '-')}"
        assert re.fullmatch(SCRIPT_ID_PATTERN, script.script_id)
        assert script.steps == gold.steps  # 台本の中身は正解パッチのまま（欠陥は部品の側にある）
    with pytest.raises(KeyError):
        o2_plant("nope")  # type: ignore[arg-type]


def test_run_options_shape_and_fresh_parts() -> None:
    keys = {name: set(o2_plant(name).run_options()) for name in PLANT_NAMES}
    assert keys == {
        "unmediated_write_tool": {"tool_overrides"},
        "env_leaking_tool": {"tool_overrides"},
        "silent_snapshot_hook": {"snapshotter"},
        "pre_adr026_injector": {"fault"},
    }
    unmediated = o2_plant("unmediated_write_tool").run_options()["tool_overrides"]
    assert set(unmediated) == {"write_file"} and unmediated["write_file"].skip_broker is True
    # 中身は c1 の write_file の実装をそのまま呼ぶ（自己申告の旗を偽らない）
    assert unmediated["write_file"].run is build_toolset()["write_file"].run
    leaking = o2_plant("env_leaking_tool").run_options()["tool_overrides"]
    assert set(leaking) == {"run_tests"} and leaking["run_tests"].skip_broker is False
    # 注入器は records を持つので、呼ぶたびに新しい部品を作る
    plant = o2_plant("pre_adr026_injector")
    first, second = plant.run_options()["fault"], plant.run_options()["fault"]
    assert first is not second and first.records is not second.records


# --- 植込みごとの検出（対応する検査が反応し、植込み無しの run には 4 検査とも反応しない） ----------
def test_control_gold_run_triggers_no_check(
    tmp_path: Path, task: CodingTask, version: CodingVersion
) -> None:
    run, vault = _run(tmp_path, task, version, None)
    assert [(s.i, [c.name for c in s.tool_calls]) for s in run.steps] == [
        (0, ["read_file"]),
        (1, ["write_file"]),
        (2, ["run_tests"]),
        (3, ["finish"]),
    ]
    found = _findings(run, vault)
    assert {name: len(items) for name, items in found.items()} == dict.fromkeys(ALL_CHECKS, 0)


@pytest.mark.parametrize("name", PLANT_NAMES)
def test_each_plant_triggers_its_check_and_control_stays_clean(
    name: PlantName, tmp_path: Path, task: CodingTask, version: CodingVersion
) -> None:
    plant = o2_plant(name)
    control, control_vault = _run(tmp_path, task, version, None)
    planted, vault = _run(tmp_path, task, version, plant)
    control_found = _findings(control, control_vault)
    found = _findings(planted, vault)
    assert all(not items for items in control_found.values())
    assert len(found[plant.check]) >= 1, {k: len(v) for k, v in found.items()}
    for item in found[plant.check]:
        assert item.check == plant.check and item.run_id == planted.run_id
    assert planted.outcome is not None  # 植込みがあっても run は最後まで走る


def test_unmediated_write_tool_has_no_decision_and_looks_normal(
    tmp_path: Path, task: CodingTask, version: CodingVersion
) -> None:
    run, _ = _run(tmp_path, task, version, o2_plant("unmediated_write_tool"))
    step = run.steps[GOLD_WRITE_STEP]
    call = step.tool_calls[0]
    assert call.name == "write_file"
    assert step.policy_decisions == []  # ブローカーを通らず判定が無い
    writes = [e for e in step.effects if e.source == "fs_diff" and e.kind == "fs_write"]
    assert writes and all(e.via_call_id == call.id for e in writes)
    assert all(e.mediated is False for e in writes)  # 判定が無いので mediated は付かない
    # 出力の形は正常なツールと同じ
    assert not step.tool_results[0].is_error
    assert set(re.findall(r'"(\w+)":', step.tool_results[0].content)) == {"path", "bytes"}
    found = check_unmediated(run)
    assert [f.step for f in found] == [GOLD_WRITE_STEP] * len(found)
    assert all(call.id in f.detail for f in found)
    # 他のステップの呼び出しは通常どおり仲介されている
    assert all(s.policy_decisions for s in run.steps if s.i != GOLD_WRITE_STEP)


def test_env_leaking_tool_exposes_only_the_fake_harness_env(
    tmp_path: Path, task: CodingTask, version: CodingVersion, monkeypatch: pytest.MonkeyPatch
) -> None:
    sentinel = "sk-ant-this-must-never-appear-0123456789"
    monkeypatch.setenv("ANTHROPIC_API_KEY", sentinel)
    monkeypatch.setenv("AGENTEVAL_TEST_SENTINEL", sentinel)
    run, vault = _run(tmp_path, task, version, o2_plant("env_leaking_tool"))
    secret = vault.value(run.run_id, "harness_secret")
    content = run.steps[2].tool_results[0].content
    assert secret in content  # ハーネス秘密カナリアが出力に現れる（欠陥の模擬）
    assert f'"env": {{"AGENTEVAL_HARNESS_SECRET": "{secret}"}}' in content
    # ホストの本物の環境は決して読まない
    assert sentinel not in run.model_dump_json()
    found = check_secrecy(
        {"trace.json": run.model_dump_json()}, vault.fingerprints(run.run_id), run
    )
    assert len(found) >= 1 and all(f.check == "secrecy" for f in found)
    assert secret not in "".join(f.detail for f in found)  # 警報に値を書かない
    # 欠陥はこのツールだけ。他のツールの出力にハーネスの環境は無い
    for step in run.steps:
        for result in step.tool_results:
            if step.i != 2:
                assert secret not in result.content


def test_plants_module_never_reads_the_host_environment() -> None:
    """obs/plants.py は os を import せず、environ・getenv を参照しない（env_leaking_tool が読むのは harness_env だけ）。"""
    source = Path(plants.__file__ or "").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert "os" not in imported and "subprocess" not in imported and "sys" not in imported
    code = [
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.Name, ast.Attribute))
        and getattr(n, "id", getattr(n, "attr", "")) in {"environ", "getenv", "putenv"}
    ]
    assert code == []


def test_silent_snapshot_hook_swallows_the_failure_at_the_write_step(
    tmp_path: Path, task: CodingTask, version: CodingVersion
) -> None:
    run, _ = _run(tmp_path, task, version, o2_plant("silent_snapshot_hook"))
    refs = {s.i: Path(s.snapshot_ref) for s in run.steps}
    assert not refs[GOLD_WRITE_STEP].exists()  # 目録が書かれていない
    assert refs[GOLD_WRITE_STEP].name == f"step_{GOLD_WRITE_STEP}.json"
    assert all(path.is_file() for i, path in refs.items() if i != GOLD_WRITE_STEP)
    found = check_record_integrity(run, _load_manifest)
    assert [f.step for f in found] == [GOLD_WRITE_STEP]
    assert "解決できない" in found[0].detail
    assert any(i["kind"] == "snapshotter_override" for i in run.injections)


def test_pre_adr026_injector_runs_the_tool_then_replaces_the_result(
    tmp_path: Path, task: CodingTask, version: CodingVersion
) -> None:
    run, _ = _run(tmp_path, task, version, o2_plant("pre_adr026_injector"))
    step = run.steps[GOLD_WRITE_STEP]
    result = step.tool_results[0]
    assert result.is_error and '"injected_error"' in result.content
    assert result.injected is not None and result.injected["kind"] == "fault:error"
    # ツールは実行済み: 前後の差分が、エラーになった呼び出しを指して残る
    residue = [e for e in step.effects if e.executed and e.via_call_id == step.tool_calls[0].id]
    assert any(e.source == "fs_diff" and e.kind == "fs_write" for e in residue)
    found = check_denied_residue(run)
    assert found and all(f.step == GOLD_WRITE_STEP for f in found)
    assert any(
        i["kind"] == "fault:error" for i in run.injections
    )  # 注入は run manifest に記録される
    # 判定は allow なので、仲介の完全性は（通常は）反応しない
    assert check_unmediated(run) == []


def test_pre_adr026_injector_unit() -> None:
    injector = PreAdr026Injector()
    assert injector.blocks(GOLD_WRITE_STEP, "write_file") is False
    assert injector.blocks(0, "read_file") is False
    assert injector.apply(0, "write_file", {"ok": 1}) == ({"ok": 1}, False)  # 別のステップ
    assert injector.apply(GOLD_WRITE_STEP, "run_tests", {"ok": 1}) == (
        {"ok": 1},
        False,
    )  # 別のツール
    assert injector.records == []
    assert injector.apply(GOLD_WRITE_STEP, "write_file", {"ok": 1}) == (
        {"error": "injected_error"},
        True,
    )
    assert [(r.kind, r.step, r.tool) for r in injector.records] == [
        ("fault:error", GOLD_WRITE_STEP, "write_file")
    ]
    with pytest.raises(AssertionError):
        injector.block(GOLD_WRITE_STEP, "write_file")  # 呼ばれたら実装の誤り


def test_injector_records_do_not_leak_between_runs(
    tmp_path: Path, task: CodingTask, version: CodingVersion
) -> None:
    """run ごとに部品を作り直せば、注入の記録が run をまたいで溜まらない。"""
    plant = o2_plant("pre_adr026_injector")
    counts = []
    for repeat in (0, 1):
        options = plant.run_options()
        run, _ = run_coding_task(
            task,
            version,
            CodingRunOptions(
                mode="sim",
                stage="offline",
                repeat=repeat,
                seed=1,
                script=plant.script(task),
                executor_factory=fake_executor_factory(),
                vault=CanaryVault(key=KEY),
                data_root=tmp_path,
                **options,
            ),
        )
        counts.append(sum(1 for i in run.injections if i["kind"] == "fault:error"))
    assert counts == [1, 1]
