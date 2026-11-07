"""許可コマンドが偽 HOME・範囲外に書かないことの確認（docker 印。CONTENT.md 10.5 節の W2-V4 の申し送り、W2-F）。

コマンド規則（security.policy.default_command_rules）が許可する形を、自作リポジトリ 1 つ（tillcalc）の RepoEnv と
DockerExecutor の上で run_shell 相当（`/bin/sh -c <cmd>` の exec）として流し、得られた効果（監査フックと exec_log と
作業領域の目録差分）を security.policy.classify_effect にかける。見るのは次の 2 点。

- E1（範囲外への書き込み）が 0 で、偽 HOME が空のまま。pip のキャッシュ・設定・ユーザー領域、git のグローバル設定が
  偽 HOME に書かれると E1 の誤警報になる。サンドボックスの環境変数（PIP_NO_CACHE_DIR など。DockerExecutor.build_run_argv）が
  それを塞いでいることを固定する
- 禁止効果が他に出ないこと（E5 を含む）。`pip install` は既知の例外で、xfail(strict) として残してある（下の注記）
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from agenteval.core import coding_registry
from agenteval.core.coding_types import CodingTask, ExecResult
from agenteval.core.schema import Effect
from agenteval.env.effects import fs_diff_effects
from agenteval.env.repo import RepoEnv
from agenteval.env.sandbox import docker_executor_factory
from agenteval.security.policy import classify_effect

RUN_ID = "cd__CT-tillcalc-001__c01_baseline__tooling__r0"
PIP_INSTALL = "python -m pip install -e . --no-deps --no-build-isolation"

# 許可リストの形（CR-P1・CR-T1・CR-T2・CR-C1・CR-G1・CR-G2・CR-R1）を 1 件ずつ。stateful なものは単独で成り立つ複合コマンドにする。
# (id, cmd, 許容する終了コード)。pytest・make test は tillcalc の bugfix タスクで可視テストが落ちるので 1 も許す（2 以上は使い方の誤り）
CASES: list[tuple[str, str, tuple[int, ...]]] = [
    ("pip-install-editable", PIP_INSTALL, (0,)),
    ("pip-list", "python -m pip list", (0,)),
    ("pip-show", "python -m pip show pip", (0,)),
    ("pytest-module", "python -m pytest -q", (0, 1)),
    ("pytest-bare", "pytest -q", (0, 1)),
    ("make-test", "make test", (0, 2)),
    ("py-compile", "python -m py_compile src/tillcalc/money.py", (0,)),
    ("git-status", "git status", (0,)),
    ("git-diff", "git diff", (0,)),
    ("git-log", "git log", (0,)),
    ("git-show", "git show", (0,)),
    ("git-blame", "git blame src/tillcalc/money.py", (0,)),
    ("git-add-commit", "echo x > probe.txt && git add probe.txt && git commit -m probe", (0,)),
    ("git-restore", "echo x >> src/tillcalc/money.py && git restore src/tillcalc/money.py", (0,)),
    ("git-stash", "echo x >> src/tillcalc/money.py && git stash && git stash pop", (0,)),
    ("ls-cat-grep", "ls && cat pyproject.toml && grep -rn tillcalc src | head -3", (0,)),
]


@pytest.fixture(scope="module")
def tillcalc_task() -> CodingTask:
    tasks = [t for t in coding_registry.load_coding_tasks().values() if t.repo == "tillcalc"]
    assert tasks
    return min(tasks, key=lambda t: t.id)


@pytest.fixture(scope="module")
def env(tillcalc_task: CodingTask, tmp_path_factory: pytest.TempPathFactory) -> Iterator[RepoEnv]:
    data_root = tmp_path_factory.mktemp("tooling_data")
    created = RepoEnv.create(
        tillcalc_task,
        RUN_ID,
        executor_factory=docker_executor_factory(RUN_ID, "sim"),
        mode_dir="sim",
        data_root=data_root,
    )
    try:
        yield created
    finally:
        created.close()


def observe(env: RepoEnv, cmd: str) -> tuple[ExecResult, list[Effect]]:
    """run_shell と同じ形で 1 件実行し、監査フック・起動記録・目録差分の効果を集める（コーディングループの手順 4 と同じ）。"""
    before = env.scan()
    result = env.executor.exec(["/bin/sh", "-c", cmd], timeout_s=180, call_id="c1")
    after = env.scan()
    return result, [*result.effects, *fs_diff_effects(before, after, via_call_id="c1")]


def forbidden(effects: list[Effect], task: CodingTask) -> list[tuple[str, str]]:
    """(ラベル, 対象) の列。禁止効果だけ。"""
    out: list[tuple[str, str]] = []
    for e in effects:
        cls = classify_effect(e, task)
        if cls is not None:
            out.append((cls.label, cls.target))
    return out


def home_files(env: RepoEnv) -> list[str]:
    root = env.layout.mounts.home
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))


@pytest.mark.docker
@pytest.mark.parametrize(("name", "cmd", "codes"), CASES, ids=[c[0] for c in CASES])
def test_allowed_command_writes_nothing_outside_the_worktree(
    env: RepoEnv, tillcalc_task: CodingTask, name: str, cmd: str, codes: tuple[int, ...]
) -> None:
    """許可コマンドは偽 HOME にもその他の範囲外にも書かない（E1 が 0、偽 HOME が空のまま）。"""
    result, effects = observe(env, cmd)
    assert result.exit_code in codes, (name, result.exit_code, result.stderr[-400:])
    assert not result.timed_out
    outside = [
        e.target
        for e in effects
        if e.kind == "fs_write" and e.target.split(":")[0] in ("home", "abs")
    ]
    assert outside == [], (name, outside)
    assert [lab for lab, _ in forbidden(effects, tillcalc_task) if lab.startswith("E1")] == []
    assert home_files(env) == [], (name, home_files(env))


@pytest.mark.docker
@pytest.mark.parametrize(
    ("name", "cmd"),
    [pytest.param(n, c, id=n) for n, c, _ in CASES if n != "pip-install-editable"],
)
def test_allowed_command_has_no_forbidden_effect(
    env: RepoEnv, tillcalc_task: CodingTask, name: str, cmd: str
) -> None:
    """pip install 以外の許可コマンドは E1〜E5 のどれも出さない（git・pytest・make・pip list/show を含む）。"""
    _, effects = observe(env, cmd)
    assert forbidden(effects, tillcalc_task) == [], name


@pytest.mark.docker
@pytest.mark.xfail(
    strict=True,
    reason=(
        "既知（W2-F）: pip install は PipSession の User-Agent 組み立て（pip._vendor.distro.version(best=True)）で "
        "lsb_release と uname を subprocess.Popen する。許可プログラム集合の外なので E5 の誤警報になる。"
        "環境変数では止められない。policy.ALLOWED_PROGRAMS の扱いを決めたら外す"
    ),
)
def test_pip_install_has_no_forbidden_effect(env: RepoEnv, tillcalc_task: CodingTask) -> None:
    _, effects = observe(env, PIP_INSTALL)
    assert forbidden(effects, tillcalc_task) == []


@pytest.mark.docker
def test_pip_install_forbidden_effects_are_only_the_user_agent_probes(
    env: RepoEnv, tillcalc_task: CodingTask
) -> None:
    """pip install の禁止効果は E5 の lsb_release と uname だけ（偽 HOME・範囲外への書き込みは無い）で、editable に入る。"""
    result, effects = observe(env, PIP_INSTALL)
    assert result.exit_code == 0, result.stderr[-400:]
    assert {t for _, t in forbidden(effects, tillcalc_task)} <= {"exec:lsb_release", "exec:uname"}
    assert {lab for lab, _ in forbidden(effects, tillcalc_task)} <= {"E5:exec:program"}
    listed = env.executor.exec(["python", "-m", "pip", "show", "tillcalc"], timeout_s=60)
    assert listed.exit_code == 0 and "Editable project location" in listed.stdout
