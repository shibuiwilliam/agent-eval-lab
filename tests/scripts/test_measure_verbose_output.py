"""scripts/measure_verbose_output.py のテスト。

Docker を使わない部分は FakeExecutor で試す（pytest の引数と切り詰めが版の設定から来ること、集計）。
Docker を使う 1 件の結合テストだけ `@pytest.mark.docker`。生成物は tmp_path の下だけに書く。
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

from agenteval.core.coding_registry import load_coding_tasks, load_coding_versions
from agenteval.core.coding_types import CodingTask, ExecResult, ExecutorFactory, SandboxMounts
from agenteval.env.repo import RepoEnv
from agenteval.env.sandbox import (
    FakeCall,
    FakeExecutor,
    cleanup_containers,
    docker_executor_factory,
    fake_executor_factory,
)

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "measure_verbose_output.py"
TASK_ID = "CT-tillcalc-004"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("measure_verbose_output", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["measure_verbose_output"] = module
    spec.loader.exec_module(module)
    return module


mvo = _load()


@pytest.fixture
def task() -> CodingTask:
    return load_coding_tasks()[TASK_ID]


def _responder(lines: int) -> Callable[[FakeCall, SandboxMounts], ExecResult]:
    """pytest の標準出力として `lines` 行を返す（JUnit XML は無いので件数は出力から数える）。"""

    def respond(call: FakeCall, mounts: SandboxMounts) -> ExecResult:
        body = "\n".join(f"line {i}" for i in range(lines - 1))
        return ExecResult(
            argv=call.argv,
            cwd=call.cwd,
            exit_code=1,
            stdout=f"{body}\n1 failed, 3 passed in 0.1s",
            stderr="",
            duration_ms=1,
        )

    return respond


def _env(task: CodingTask, tmp_path: Path, factory: ExecutorFactory) -> RepoEnv:
    return RepoEnv.create(
        task, "cd__measure__unit__r0", executor_factory=factory, mode_dir="sim", data_root=tmp_path
    )


def _pytest_argvs(env: RepoEnv) -> list[list[str]]:
    assert isinstance(env.executor, FakeExecutor)
    return [c.argv for c in env.executor.calls if c.argv[:3] == ["python", "-m", "pytest"]]


def test_pytest_args_come_from_the_version_harness(task: CodingTask, tmp_path: Path) -> None:
    versions = load_coding_versions()
    env = _env(task, tmp_path, fake_executor_factory(_responder(5)))
    for vid in (mvo.BASELINE_VERSION, mvo.VERBOSE_VERSION):
        mvo.measure_once(env, task, versions[vid], "mutant", "all")
        mvo.measure_once(env, task, versions[vid], "mutant", "failing")
    c01_all, c01_fail, c04_all, c04_fail = _pytest_argvs(env)
    assert "-q" in c01_all and "--tb=short" in c01_all and "-vv" not in c01_all
    assert {"-vv", "--tb=long", "-rA"} <= set(c04_all) and "-q" not in c04_all
    assert c01_all[-1] == "tests" and c04_all[-1] == "tests"  # 引数なし = 可視テスト全体
    assert c01_fail[-1] == c04_fail[-1] == "tests/test_allocate.py::test_split_even_001"
    env.close()


def test_truncation_follows_test_output_limit(task: CodingTask, tmp_path: Path) -> None:
    versions = load_coding_versions()
    limit = versions[mvo.BASELINE_VERSION].harness.test_output_limit
    assert limit is not None and versions[mvo.VERBOSE_VERSION].harness.test_output_limit is None
    env = _env(task, tmp_path, fake_executor_factory(_responder(limit + 40)))
    base = mvo.measure_once(env, task, versions[mvo.BASELINE_VERSION], "mutant", "all")
    verb = mvo.measure_once(env, task, versions[mvo.VERBOSE_VERSION], "mutant", "all")
    assert base.truncated and base.shown_chars < base.raw_chars
    assert not verb.truncated and verb.shown_chars == verb.raw_chars == base.raw_chars
    assert base.payload_chars > base.shown_chars
    env.close()


def test_split_patch_and_gold_application(task: CodingTask, tmp_path: Path) -> None:
    two = "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n--- a/y/z.py\n+++ b/y/z.py\n@@ -1 +1 @@\n-c\n+d\n"
    assert mvo.split_patch_by_file(two) == {
        "x.py": "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n",
        "y/z.py": "--- a/y/z.py\n+++ b/y/z.py\n@@ -1 +1 @@\n-c\n+d\n",
    }
    env = _env(task, tmp_path, fake_executor_factory())
    rel = task.gold_files[0]
    before = env.read_area_file(f"repo:{rel}")
    assert mvo.apply_gold_patch(env, task) == [rel]
    after = env.read_area_file(f"repo:{rel}")
    correct = (mvo.REPO_FIXTURE_ROOT / task.repo / "repo" / rel).read_text(encoding="utf-8")
    assert before != after and after == correct  # 正解側は repo/ の正しいコードと一致する
    env.close()


def test_measure_task_runs_both_sides_for_both_versions(task: CodingTask, tmp_path: Path) -> None:
    versions = load_coding_versions()
    rows = mvo.measure_task(
        task,
        [versions[mvo.BASELINE_VERSION], versions[mvo.VERBOSE_VERSION]],
        executor_factory_of=lambda _run_id: fake_executor_factory(_responder(5)),
        data_root=tmp_path,
        require_isolated=False,  # fake は network_isolated=False
    )
    assert len(rows) == 8  # 2 側 x 2 subset x 2 版
    assert {(r.side, r.subset, r.version_id) for r in rows} == {
        (s, u, v)
        for s in ("mutant", "gold")
        for u in ("all", "failing")
        for v in (mvo.BASELINE_VERSION, mvo.VERBOSE_VERSION)
    }
    assert all(r.task_id == TASK_ID for r in rows)
    # 作業領域は片付く（再実行できる）
    assert not any((tmp_path / "work" / "sim").glob("cd__measure__*"))


def _row(task_id: str, side: str, subset: str, version: str, shown: int, payload: int) -> object:
    return mvo.Row(
        task_id=task_id,
        side=side,
        subset=subset,
        version_id=version,
        selector=None,
        exit_code=0,
        n_passed=1,
        n_failed=0,
        n_error=0,
        raw_chars=shown,
        shown_chars=shown,
        payload_chars=payload,
        truncated=False,
        timed_out=False,
    )


def test_summarize_diffs_totals_means_and_tokens() -> None:
    rows = []
    for tid, (b, v) in {"A": (100, 400), "B": (200, 800)}.items():
        for side in ("mutant", "gold"):
            for subset in ("all", "failing"):
                rows.append(_row(tid, side, subset, "c01_baseline", b, b + 10))
                rows.append(_row(tid, side, subset, "c04_verbose_ctx", v, v + 10))
    s = mvo.summarize(rows, chars_per_token=2.0)
    assert len(s["per_task"]) == 8
    t = s["totals"]["mutant/all"]
    assert t["n_tasks"] == 2
    assert t["shown_chars"] == {
        "diff_total": 900,  # (400-100) + (800-200)
        "diff_mean": 450.0,
        "diff_total_tokens": 450.0,
        "diff_mean_tokens": 225.0,
    }
    assert t["payload_chars"]["diff_total"] == 900
    assert set(s["totals"]) == {"mutant/all", "mutant/failing", "gold/all", "gold/failing"}
    assert s["sums_by_version"]["gold/failing/c04_verbose_ctx"]["shown_chars"] == 1200


def test_summarize_skips_tasks_missing_a_version() -> None:
    rows = [_row("A", "mutant", "all", "c01_baseline", 10, 20)]
    s = mvo.summarize(rows)
    assert s["per_task"] == [] and s["totals"]["mutant/all"]["n_tasks"] == 0


@pytest.mark.docker
def test_docker_measurement_of_one_task(task: CodingTask, tmp_path: Path) -> None:
    """本番と同じ Docker の経路（--network none）で 1 タスクを測る。"""
    started: list[str] = []

    def factory_of(run_id: str) -> ExecutorFactory:
        started.append(run_id)
        return docker_executor_factory(run_id, "sim")

    try:
        result = mvo.run_measurement([TASK_ID], executor_factory_of=factory_of, data_root=tmp_path)
    finally:
        for run_id in started:
            cleanup_containers(run_id)
    rows = {(r["side"], r["subset"], r["version_id"]): r for r in result["rows"]}
    base_m = rows[("mutant", "all", "c01_baseline")]
    assert base_m["n_failed"] == len(task.failing_visible_tests) and base_m["n_passed"] > 0
    gold = rows[("gold", "all", "c04_verbose_ctx")]
    assert gold["n_failed"] == 0 and gold["exit_code"] == 0
    for side in ("mutant", "gold"):
        for subset in ("all", "failing"):
            assert (
                rows[(side, subset, "c04_verbose_ctx")]["shown_chars"]
                > rows[(side, subset, "c01_baseline")]["shown_chars"]
            )
    assert result["summary"]["totals"]["mutant/all"]["shown_chars"]["diff_total"] > 0
