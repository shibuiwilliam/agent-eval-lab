"""サンドボックス（DockerExecutor・FakeExecutor・監査フック）のテスト（SPEC.md 5 節）。

Docker 印の無いテストは docker を起動しない（docker CLI は fake の runner に差し替える。
FakeExecutor のテストもホストのプロセスを起動しない）。Docker 印のテストはイメージが無ければ自動でスキップされる。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import types
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest

from agenteval.core.coding_types import (
    Area,
    ExecResult,
    Executor,
    HoneypotSpec,
    ManifestEntry,
    SandboxMounts,
    SnapshotManifest,
)
from agenteval.core.schema import ToolCall
from agenteval.env import effects as fx
from agenteval.env.sandbox import (
    PAYLOAD_DIR,
    DockerConfig,
    DockerExecutor,
    FakeCall,
    FakeExecutor,
    SandboxError,
    cleanup_containers,
    docker_available,
    docker_executor_factory,
    fake_executor_factory,
)
from agenteval.security.canary import CanaryVault

RUN = "cd__CT-demo-001__c01_baseline__unit__r0"


def make_mounts(root: Path) -> SandboxMounts:
    dirs = {name: root / name for name in ("repo", "home", "tmp", "effects", "reports")}
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)
    return SandboxMounts(**dirs)


@pytest.fixture
def mounts(tmp_path: Path) -> SandboxMounts:
    return make_mounts(tmp_path / "work")


class FakeDocker:
    """docker CLI の代わりに引数を記録し、台本どおりの CompletedProcess を返す runner。"""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.exec_result: subprocess.CompletedProcess[str] | None = None
        self.exec_raises: BaseException | None = None
        self.run_returncode = 0
        self.ps_stdout = ""
        self.on_exec: Any = None

    def __call__(self, argv: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        argv = list(argv)
        self.calls.append(argv)
        sub = argv[1]
        if sub == "run":
            return subprocess.CompletedProcess(argv, self.run_returncode, "cid\n", "boom")
        if sub == "ps":
            return subprocess.CompletedProcess(argv, 0, self.ps_stdout, "")
        if sub == "exec":
            self.exec_kwargs = kwargs
            if self.on_exec is not None:
                self.on_exec(argv)
            if self.exec_raises is not None:
                raise self.exec_raises
            return self.exec_result or subprocess.CompletedProcess(argv, 0, "out", "err")
        return subprocess.CompletedProcess(argv, 0, "", "")

    def subcommands(self) -> list[str]:
        return [c[1] for c in self.calls]


def make_executor(mounts: SandboxMounts, docker: FakeDocker, **kw: Any) -> DockerExecutor:
    return DockerExecutor(mounts, run_id=RUN, mode="sim", runner=docker, **kw)


# --- docker run の引数 ------------------------------------------------------
def pairs(argv: list[str], flag: str) -> list[str]:
    """argv の中の `flag value` の value をすべて返す。"""
    return [argv[i + 1] for i, a in enumerate(argv) if a == flag]


def test_run_argv_follows_spec(mounts: SandboxMounts) -> None:
    ex = make_executor(mounts, FakeDocker())
    argv = ex.build_run_argv()
    expected_name = "agenteval-" + hashlib.sha256(("sim" + RUN).encode()).hexdigest()[:16]
    assert ex.container_name == expected_name
    assert argv[:3] == ["docker", "run", "-d"]
    assert pairs(argv, "--name") == [expected_name]
    assert pairs(argv, "--label") == [f"agenteval.run_id={RUN}", "agenteval.mode=sim"]
    assert pairs(argv, "--network") == ["none"]
    assert "--read-only" in argv
    assert pairs(argv, "--user") == ["1000:1000"]
    assert pairs(argv, "--cpus") == ["1"]
    assert pairs(argv, "--memory") == ["1g"]
    assert pairs(argv, "--pids-limit") == ["256"]
    assert pairs(argv, "--cap-drop") == ["ALL"]
    assert pairs(argv, "--security-opt") == ["no-new-privileges"]
    assert pairs(argv, "-w") == ["/work/repo"]
    assert argv[-3:] == ["agenteval-sandbox:py3.13", "sleep", "infinity"]


def test_run_argv_mounts_exactly_three_writable_areas_plus_effect_dirs(
    mounts: SandboxMounts,
) -> None:
    argv = make_executor(mounts, FakeDocker()).build_run_argv()
    volumes = pairs(argv, "-v")
    parsed = [v.rsplit(":", 2) for v in volumes]  # [host, container, flag]
    by_container = {c: (h, f) for h, c, f in parsed}
    assert set(by_container) == {
        "/work/repo",
        "/home/agent",
        "/tmp",
        "/var/agenteval/effects",
        "/var/agenteval/reports",
        "/usr/local/lib/python3.13/site-packages/sitecustomize.py",
    }
    writable = {c for c, (_, f) in by_container.items() if f == "rw"}
    assert writable == {
        "/work/repo",
        "/home/agent",
        "/tmp",
        "/var/agenteval/effects",
        "/var/agenteval/reports",
    }
    # 監査フックは読み取り専用で実行時にマウントする（イメージに焼き込まない）
    hook_host, hook_flag = by_container["/usr/local/lib/python3.13/site-packages/sitecustomize.py"]
    assert hook_flag == "ro"
    assert Path(hook_host) == (PAYLOAD_DIR / "sitecustomize.py").resolve()
    assert Path(by_container["/work/repo"][0]) == mounts.repo.resolve()
    assert Path(by_container["/home/agent"][0]) == mounts.home.resolve()
    assert Path(by_container["/tmp"][0]) == mounts.tmp.resolve()
    # 余計な権限を渡さない
    for forbidden in ("--privileged", "-p", "--publish", "--network=host", "--tmpfs"):
        assert forbidden not in argv
    assert not any("docker.sock" in v for v in volumes)


def test_run_argv_environment(mounts: SandboxMounts) -> None:
    argv = make_executor(mounts, FakeDocker()).build_run_argv()
    env = dict(e.split("=", 1) for e in pairs(argv, "-e"))
    assert env["HOME"] == "/home/agent"
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"
    assert env["AGENTEVAL_EFFECT_LOG_DIR"] == "/var/agenteval/effects"
    assert env["PATH"].startswith("/work/repo/.venv/bin:/usr/local/bin:")
    assert env["VIRTUAL_ENV"] == "/work/repo/.venv"
    assert (env["PIP_NO_CACHE_DIR"], env["PIP_DISABLE_PIP_VERSION_CHECK"], env["PIP_NO_INPUT"]) == (
        "1",
        "1",
        "1",
    )
    # ホストの環境変数（API キーなど）は渡さない
    assert not any("ANTHROPIC" in key for key in env)


def test_run_argv_honors_config(mounts: SandboxMounts, tmp_path: Path) -> None:
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "sitecustomize.py").write_text("# 別のフック\n")
    cfg = DockerConfig(
        image="img:x", cpus=2.5, memory="512m", pids_limit=64, user="1001:1001", payload_dir=payload
    )
    argv = make_executor(mounts, FakeDocker(), config=cfg).build_run_argv()
    assert pairs(argv, "--cpus") == ["2.5"]
    assert pairs(argv, "--memory") == ["512m"]
    assert pairs(argv, "--pids-limit") == ["64"]
    assert pairs(argv, "--user") == ["1001:1001"]
    assert argv[-3] == "img:x"
    assert any(v.startswith(f"{payload.resolve()}/sitecustomize.py:") for v in pairs(argv, "-v"))


def test_run_argv_rejects_unmountable_path(tmp_path: Path) -> None:
    bad = make_mounts(tmp_path / "a:b")
    with pytest.raises(SandboxError):
        make_executor(bad, FakeDocker()).build_run_argv()


# --- docker exec の引数 -----------------------------------------------------
def test_exec_argv(mounts: SandboxMounts) -> None:
    ex = make_executor(mounts, FakeDocker())
    argv = ex.build_exec_argv(
        ["/bin/sh", "-c", "make test"],
        timeout_s=60,
        cwd="sub/dir",
        call_id="toolu_01",
        env={"K": "V"},
    )
    assert argv == [
        "docker", "exec",
        "-e", "AGENTEVAL_CALL_ID=toolu_01",
        "-e", "K=V",
        "-w", "/work/repo/sub/dir", ex.container_name,
        "timeout", "-k", "2", "60",
        "/bin/sh", "-c", "make test",
    ]  # fmt: skip
    plain = ex.build_exec_argv(["ls"], timeout_s=1.5)
    assert plain == [
        "docker",
        "exec",
        "-w",
        "/work/repo",
        ex.container_name,
        "timeout",
        "-k",
        "2",
        "1.5",
        "ls",
    ]


@pytest.mark.parametrize("cwd", ["/etc", "..", "a/../../b", "../x"])
def test_cwd_must_be_relative_without_dotdot(mounts: SandboxMounts, cwd: str) -> None:
    docker = FakeDocker()
    ex = make_executor(mounts, docker)
    ex.start()
    with pytest.raises(ValueError):
        ex.exec(["ls"], timeout_s=1, cwd=cwd)
    assert "exec" not in docker.subcommands()
    ex.stop()


# --- ライフサイクル（fake の docker） --------------------------------------
def test_start_stop_lifecycle_and_idempotence(mounts: SandboxMounts) -> None:
    docker = FakeDocker()
    ex = make_executor(mounts, docker)
    assert (ex.kind, ex.network_isolated, ex.mounts) == ("docker", True, mounts)
    assert isinstance(ex, Executor)
    with pytest.raises(RuntimeError):
        ex.exec(["ls"], timeout_s=1)
    ex.start()
    ex.start()  # 冪等
    assert docker.subcommands().count("run") == 1
    ex.exec(["ls"], timeout_s=1)
    ex.stop()
    ex.stop()  # 冪等
    assert docker.subcommands().count("rm") == 2  # 起動前の取り残し掃除と、stop の 1 回
    assert docker.calls[-1][:3] == ["docker", "rm", "-f"]
    with pytest.raises(RuntimeError):
        ex.exec(["ls"], timeout_s=1)
    with pytest.raises(RuntimeError):
        ex.start()


def test_context_manager_stops(mounts: SandboxMounts) -> None:
    docker = FakeDocker()
    with make_executor(mounts, docker) as ex:
        ex.exec(["ls"], timeout_s=1)
    assert docker.calls[-1][:3] == ["docker", "rm", "-f"]


def test_start_failure_raises_sandbox_error(mounts: SandboxMounts) -> None:
    docker = FakeDocker()
    docker.run_returncode = 125
    ex = make_executor(mounts, docker)
    with pytest.raises(SandboxError, match="boom"):
        ex.start()
    with pytest.raises(RuntimeError):
        ex.exec(["ls"], timeout_s=1)


def test_start_without_payload_hook_raises(mounts: SandboxMounts, tmp_path: Path) -> None:
    cfg = DockerConfig(payload_dir=tmp_path / "missing")
    with pytest.raises(SandboxError):
        make_executor(mounts, FakeDocker(), config=cfg).start()


def test_exec_result_and_exec_log(mounts: SandboxMounts) -> None:
    docker = FakeDocker()
    docker.exec_result = subprocess.CompletedProcess([], 3, "so", "se")
    with make_executor(mounts, docker) as ex:
        result = ex.exec(["/bin/sh", "-c", "exit 3"], timeout_s=5, call_id="c1")
    assert (result.exit_code, result.stdout, result.stderr, result.timed_out) == (
        3,
        "so",
        "se",
        False,
    )
    assert [(e.source, e.target, e.via_call_id) for e in result.effects] == [
        ("exec_log", "exec:sh", "c1")
    ]
    # ホスト側の timeout は timeout_s + 10
    assert docker.exec_kwargs["timeout"] == 15


@pytest.mark.parametrize("code", [124, 137])
def test_timeout_exit_codes_are_normalized(mounts: SandboxMounts, code: int) -> None:
    docker = FakeDocker()
    docker.exec_result = subprocess.CompletedProcess([], code, "", "")
    with make_executor(mounts, docker) as ex:
        result = ex.exec(["sleep", "9"], timeout_s=0.0)
    assert result.timed_out and result.exit_code == 124


def test_137_without_elapsed_timeout_is_not_a_timeout(mounts: SandboxMounts) -> None:
    docker = FakeDocker()
    docker.exec_result = subprocess.CompletedProcess([], 137, "", "")
    with make_executor(mounts, docker) as ex:
        result = ex.exec(["x"], timeout_s=600)
    assert not result.timed_out and result.exit_code == 137


def test_host_side_timeout_kills_container_and_raises(mounts: SandboxMounts) -> None:
    docker = FakeDocker()
    docker.exec_raises = subprocess.TimeoutExpired(["docker"], 11)
    ex = make_executor(mounts, docker)
    ex.start()
    with pytest.raises(SandboxError):
        ex.exec(["sleep", "99"], timeout_s=1)
    assert docker.calls[-1][:2] == ["docker", "kill"]
    assert docker.subcommands().count("run") == 1  # 作り直さない


def test_daemon_error_is_sandbox_error(mounts: SandboxMounts) -> None:
    docker = FakeDocker()
    docker.exec_result = subprocess.CompletedProcess(
        [], 1, "", "Error response from daemon: container x is not running"
    )
    with make_executor(mounts, docker) as ex, pytest.raises(SandboxError):
        ex.exec(["ls"], timeout_s=1)


# --- フックの記録の回収 -----------------------------------------------------
def hook_line(event: str, args: dict[str, Any], call_id: str | None) -> str:
    return (
        json.dumps({"v": 1, "pid": 9, "ppid": 1, "call_id": call_id, "event": event, "args": args})
        + "\n"
    )


def test_collect_hook_effects_incrementally(mounts: SandboxMounts) -> None:
    docker = FakeDocker()
    log = mounts.effects / "9-1.jsonl"
    log.write_text(hook_line("hook.start", {"argv": ["python"]}, "c1"))
    other = mounts.effects / "10-2.jsonl"
    other.write_text("")

    def on_exec(argv: list[str]) -> None:
        with log.open("a") as fh:
            fh.write(hook_line("open", {"path": "/home/agent/.aws/credentials", "mode": "r"}, "c1"))
            fh.write('{"v": 1, "event": "open", "args": {"path": "/tmp/part')  # 書きかけの行

    docker.on_exec = on_exec
    with make_executor(mounts, docker) as ex:
        first = ex.exec(["python", "x.py"], timeout_s=5, call_id="c1")
        assert [(e.source, e.kind, e.target) for e in first.effects] == [
            ("exec_log", "exec", "exec:python"),
            ("audit_hook", "exec", "exec:python"),  # hook.start
            ("audit_hook", "fs_read", "home:.aws/credentials"),
        ]
        # 書きかけの行は完成するまで読まない。完成したら 1 回だけ返し、読み取り済みの行は二度と返さない
        docker.on_exec = None
        second = ex.exec(["ls"], timeout_s=5, call_id="c2")
        assert [e.source for e in second.effects] == ["exec_log"]
        with log.open("a") as fh:
            fh.write('.txt", "mode": "w"}}\n')
        completed = ex.exec(["ls"], timeout_s=5, call_id="c2")
        assert [(e.source, e.kind, e.target) for e in completed.effects] == [
            ("exec_log", "exec", "exec:ls"),
            ("audit_hook", "fs_write", "tmp:part.txt"),
        ]
        assert [e.source for e in ex.exec(["ls"], timeout_s=5, call_id="c2").effects] == [
            "exec_log"
        ]
        # 生き残ったプロセスの記録は次の exec で拾われ、現在の呼び出しに付け替えられる
        with log.open("a") as fh:
            fh.write(hook_line("os.system", {"command": "x"}, "c1"))
        third = ex.exec(["ls"], timeout_s=5, call_id="c3")
        late = [e for e in third.effects if e.source == "audit_hook"]
        assert len(late) == 1
        assert late[0].via_call_id == "c3" and "origin_call=c1" in late[0].detail


def test_collect_ignores_symlinked_logs(mounts: SandboxMounts, tmp_path: Path) -> None:
    secret = tmp_path / "outside.jsonl"
    secret.write_text(hook_line("os.system", {"command": "x"}, "c1"))
    (mounts.effects / "link.jsonl").symlink_to(secret)
    with make_executor(mounts, FakeDocker()) as ex:
        result = ex.exec(["ls"], timeout_s=1, call_id="c1")
    assert [e.source for e in result.effects] == ["exec_log"]


# --- cleanup_containers / factory ------------------------------------------
def test_cleanup_containers_filters_by_label() -> None:
    docker = FakeDocker()
    docker.ps_stdout = "aaa\nbbb\n"
    assert cleanup_containers(RUN, runner=docker) == 2
    assert docker.calls[0] == ["docker", "ps", "-aq", "--filter", f"label=agenteval.run_id={RUN}"]
    assert docker.calls[1] == ["docker", "rm", "-f", "aaa", "bbb"]
    docker2 = FakeDocker()
    assert cleanup_containers(runner=docker2) == 0
    assert docker2.calls == [["docker", "ps", "-aq", "--filter", "label=agenteval.run_id"]]


def test_cleanup_containers_without_docker_returns_zero() -> None:
    def broken(argv: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("docker")

    assert cleanup_containers(runner=broken) == 0


def test_factories(mounts: SandboxMounts) -> None:
    docker_ex = docker_executor_factory(RUN, "sim")(mounts)
    assert isinstance(docker_ex, DockerExecutor) and docker_ex.kind == "docker"
    fake_ex = fake_executor_factory()(mounts)
    assert isinstance(fake_ex, FakeExecutor) and fake_ex.kind == "fake"
    assert docker_ex.container_name.startswith("agenteval-")


def test_docker_available_respects_skip_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTEVAL_SKIP_DOCKER", "1")
    assert docker_available() is False
    monkeypatch.delenv("AGENTEVAL_SKIP_DOCKER")
    cfg = DockerConfig(docker_bin="/nonexistent/docker-binary")
    assert docker_available(cfg) is False


# --- FakeExecutor -----------------------------------------------------------
def test_fake_executor_default_result(mounts: SandboxMounts) -> None:
    ex = FakeExecutor(mounts)
    assert (ex.kind, ex.network_isolated, ex.mounts) == ("fake", False, mounts)
    assert isinstance(ex, Executor)
    with pytest.raises(RuntimeError):
        ex.exec(["ls"], timeout_s=1)
    ex.start()
    ex.start()
    result = ex.exec(
        ["python", "-m", "pytest"], timeout_s=30, cwd="tests", call_id="c1", env={"A": "1"}
    )
    assert (result.exit_code, result.stdout, result.stderr, result.timed_out) == (0, "", "", False)
    assert [(e.source, e.target, e.via_call_id) for e in result.effects] == [
        ("exec_log", "exec:python", "c1")
    ]
    assert ex.calls == [
        FakeCall(
            argv=["python", "-m", "pytest"], cwd="tests", timeout_s=30, call_id="c1", env={"A": "1"}
        )
    ]
    ex.stop()
    ex.stop()
    with pytest.raises(RuntimeError):
        ex.exec(["ls"], timeout_s=1)


def test_fake_executor_validates_cwd(mounts: SandboxMounts) -> None:
    ex = FakeExecutor(mounts)
    ex.start()
    with pytest.raises(ValueError):
        ex.exec(["ls"], timeout_s=1, cwd="/etc")
    with pytest.raises(ValueError):
        ex.exec(["ls"], timeout_s=1, cwd="../x")
    assert ex.calls == []


def test_fake_executor_responder_can_write_into_mounts(mounts: SandboxMounts) -> None:
    def responder(call: FakeCall, m: SandboxMounts) -> ExecResult:
        (m.home / "evil").write_text("x")  # エージェントの書き込みを模す
        return ExecResult(
            argv=call.argv, cwd=call.cwd, exit_code=2, stdout="o", stderr="e", duration_ms=5
        )

    ex = fake_executor_factory(responder)(mounts)
    ex.start()
    result = ex.exec(["sh", "-c", "x"], timeout_s=1, call_id="c9")
    assert (mounts.home / "evil").read_text() == "x"
    assert result.exit_code == 2
    # exec_log が無ければ足す（DockerExecutor と同じく毎 exec で 1 件）
    assert [e.source for e in result.effects] == ["exec_log"]


def test_fake_executor_keeps_responder_exec_log(mounts: SandboxMounts) -> None:
    custom = fx.exec_log_effect(["custom"], call_id="c1")

    def responder(call: FakeCall, m: SandboxMounts) -> ExecResult:
        return ExecResult(
            argv=call.argv,
            cwd=call.cwd,
            exit_code=0,
            stdout="",
            stderr="",
            duration_ms=0,
            effects=[custom],
        )

    ex = FakeExecutor(mounts, responder=responder)
    ex.start()
    assert ex.exec(["ls"], timeout_s=1, call_id="c1").effects == [custom]


# --- 監査フック（sitecustomize）---------------------------------------------
@pytest.fixture
def hook(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """sitecustomize を別名で読み込む。環境変数が無いのでフックは登録されない（pytest の process を汚さない）。"""
    monkeypatch.delenv("AGENTEVAL_EFFECT_LOG_DIR", raising=False)
    spec = importlib.util.spec_from_file_location(
        "agenteval_sitecustomize_under_test", PAYLOAD_DIR / "sitecustomize.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module._RECORDER is None
    return module


@pytest.fixture
def recorder(hook: types.ModuleType, tmp_path: Path) -> Iterator[tuple[Any, Path]]:
    path = tmp_path / "log.jsonl"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    rec = hook.Recorder(fd)
    yield rec, path
    try:
        os.close(fd)
    except OSError:
        pass


def lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_hook_does_nothing_without_env(hook: types.ModuleType) -> None:
    assert hook.install() is None


def test_hook_records_round_trip_through_parser(
    recorder: tuple[Any, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    rec, path = recorder
    monkeypatch.setenv("AGENTEVAL_CALL_ID", "toolu_7")
    rec.handle("open", ("/home/agent/.aws/credentials", "r", 0))
    rec.handle("open", ("/tmp/x.pth", "w", 0x241))
    rec.handle("open", (b"/work/repo/a.py", None, os.O_WRONLY | os.O_CREAT))
    rec.handle("os.remove", ("/work/repo/a.py", None))
    rec.handle("os.rename", ("/work/repo/a", "/tmp/a", None, None))
    rec.handle("subprocess.Popen", ("/usr/bin/curl", ["curl", "http://x"], None, None))
    rec.handle("os.exec", ("/bin/sh", ["sh", "-c", "x"], None))
    rec.handle("os.system", ("ls",))
    rec.handle("socket.connect", (object(), ("example.com", 443)))
    rec.handle("socket.connect", (object(), "/run/x.sock"))
    rec.handle("socket.getaddrinfo", ("evil.example", 80, 0, 0, 0))
    records = lines(path)
    assert [r["event"] for r in records] == [
        "open", "open", "open", "os.remove", "os.rename", "subprocess.Popen", "os.exec",
        "os.system", "socket.connect", "socket.connect", "socket.getaddrinfo",
    ]  # fmt: skip
    assert all(
        r["v"] == 1 and r["call_id"] == "toolu_7" and r["pid"] == os.getpid() for r in records
    )
    parsed = [
        fx.parse_hook_record(line, current_call_id="toolu_7", step_call_ids={"toolu_7"})
        for line in path.read_text().splitlines()
    ]
    assert [(e.kind, e.target) for e in parsed if e is not None] == [
        ("fs_read", "home:.aws/credentials"),
        ("fs_write", "tmp:x.pth"),
        ("fs_write", "repo:a.py"),
        ("fs_write", "repo:a.py"),
        ("fs_write", "tmp:a"),
        ("exec", "exec:curl"),
        ("exec", "exec:sh"),
        ("exec", "exec:sh"),
        ("net_attempt", "net:example.com:443"),
        ("net_attempt", "unix:/run/x.sock"),
        ("net_attempt", "net:evil.example:80"),
    ]
    assert all(e is not None and e.via_call_id == "toolu_7" for e in parsed)


def test_hook_drops_noise_paths(recorder: tuple[Any, Path]) -> None:
    rec, path = recorder
    for noisy in (
        "/usr/local/lib/python3.13/os.py",
        "/usr/local/lib/python313.zip",
        "/usr/lib/x",
        "/lib/x86_64/libc.so",
        "/proc/self/status",
        "/sys/kernel",
        "/dev/null",
        "/etc/ld.so.cache",
        "/var/agenteval/effects/1.jsonl",
        "/work/repo/src/__pycache__/a.pyc",
        "/work/repo/.venv/lib/python3.13/site-packages/x.py",  # 読み取りは雑音
    ):
        rec.handle("open", (noisy, "r", 0))
    assert not path.exists() or path.read_text() == ""
    # 仮想環境への書き込みは記録する（F3 系統の観測に要る）
    rec.handle("open", ("/work/repo/.venv/lib/python3.13/site-packages/sitecustomize.py", "w", 0))
    # 通常のファイルの読み取りは記録する
    rec.handle("open", ("/work/repo/src/a.py", "r", 0))
    assert [(r["args"]["path"], r["args"]["write"]) for r in lines(path)] == [
        ("/work/repo/.venv/lib/python3.13/site-packages/sitecustomize.py", True),
        ("/work/repo/src/a.py", False),
    ]


def test_hook_resolves_relative_paths_against_cwd(
    recorder: tuple[Any, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    rec, path = recorder
    monkeypatch.chdir(tmp_path)
    rec.handle("open", ("rel/file.txt", "r", 0))
    assert lines(path)[0]["args"]["path"] == str(tmp_path / "rel" / "file.txt")


def test_hook_ignores_fd_opens_and_garbage_and_truncates(recorder: tuple[Any, Path]) -> None:
    rec, path = recorder
    rec.handle("open", (3, "r", 0))  # fd
    rec.handle("open", ())  # 引数なし
    rec.handle("os.exec", ())
    rec.handle("sys.settrace", (None,))  # 対象外
    assert not path.exists() or path.read_text() == ""
    rec.handle("os.system", ("x" * 1000,))
    assert len(lines(path)[0]["args"]["command"]) == 300
    rec.handle("subprocess.Popen", (None, ["a"] * 100, None, None))
    assert len(lines(path)[1]["args"]["argv"]) == 32


def test_hook_stops_silently_after_write_failure(recorder: tuple[Any, Path]) -> None:
    rec, _ = recorder
    os.close(rec.fd)
    rec.handle("os.system", ("ls",))  # 例外を外に出さない
    assert rec.dead
    rec.handle("os.system", ("ls",))  # 以後は何もしない


# --- Docker 印のテスト（イメージが無ければ自動でスキップ）-----------------------
def tree_manifest(m: SandboxMounts, step: int = 0) -> SnapshotManifest:
    """ホスト側のディレクトリから簡易の目録を作る（RepoEnv の代わり。テスト用）。"""
    areas: dict[Area, dict[str, ManifestEntry]] = {"repo": {}, "home": {}, "tmp": {}}
    for area, root in (("repo", m.repo), ("home", m.home), ("tmp", m.tmp)):
        for p in sorted(root.rglob("*")):
            if p.is_file() and not p.is_symlink():
                blob = hashlib.sha256(p.read_bytes()).hexdigest()
                rel = p.relative_to(root).as_posix()
                areas[area][rel] = ManifestEntry(  # type: ignore[index]
                    blob=blob,
                    digest=blob,
                    size=p.stat().st_size,
                    mode=p.stat().st_mode & 0o777,
                    cls="hashed",
                )
    return SnapshotManifest(run_id=RUN, step=step, state_hash="x", areas=areas)


@pytest.fixture
def docker_mounts(tmp_path: Path) -> SandboxMounts:
    m = make_mounts(tmp_path / "work")
    for path in (m.repo, m.home, m.tmp, m.effects, m.reports):
        path.chmod(0o777)  # コンテナの uid 1000 が書き込めるように（Linux の bind mount 用）
    return m


@pytest.fixture
def container(docker_mounts: SandboxMounts) -> Iterator[DockerExecutor]:
    ex = DockerExecutor(docker_mounts, run_id=RUN, mode="sim")
    ex.start()
    try:
        yield ex
    finally:
        ex.stop()


@pytest.mark.docker
def test_docker_container_is_isolated(container: DockerExecutor) -> None:
    info = subprocess.run(
        ["docker", "inspect", container.container_name], capture_output=True, text=True, check=True
    )
    host = json.loads(info.stdout)[0]["HostConfig"]
    assert host["NetworkMode"] == "none"
    assert host["ReadonlyRootfs"] is True
    assert host["PidsLimit"] == 256
    assert host["Memory"] == 1024**3
    assert host["CapDrop"] == ["ALL"] or host["CapDrop"] == ["CAP_ALL"]
    uid = container.exec(["id", "-u"], timeout_s=20)
    assert uid.stdout.strip() == "1000"


@pytest.mark.docker
def test_docker_writes_only_in_three_areas(container: DockerExecutor) -> None:
    ok = container.exec(["/bin/sh", "-c", "touch /work/repo/a /home/agent/b /tmp/c"], timeout_s=20)
    assert ok.exit_code == 0
    bad = container.exec(["/bin/sh", "-c", "touch /usr/x"], timeout_s=20)
    assert bad.exit_code != 0
    bad2 = container.exec(["/bin/sh", "-c", "touch /etc/x"], timeout_s=20)
    assert bad2.exit_code != 0


@pytest.mark.docker
def test_docker_passes_call_id_and_env(container: DockerExecutor) -> None:
    result = container.exec(
        ["/bin/sh", "-c", 'echo "$AGENTEVAL_CALL_ID:$FOO"; pwd'],
        timeout_s=20,
        call_id="toolu_42",
        env={"FOO": "bar"},
        cwd=".",
    )
    assert result.stdout.splitlines() == ["toolu_42:bar", "/work/repo"]


@pytest.mark.docker
def test_docker_timeout(container: DockerExecutor) -> None:
    result = container.exec(["sleep", "30"], timeout_s=1)
    assert result.timed_out and result.exit_code == 124
    assert result.duration_ms < 15_000


@pytest.mark.docker
def test_docker_network_attempt_fails_and_is_recorded(container: DockerExecutor) -> None:
    code = "import socket; socket.create_connection(('example.com', 443), timeout=3)"
    result = container.exec(["python", "-c", code], timeout_s=30, call_id="c1")
    assert result.exit_code != 0  # --network none なので必ず失敗する
    nets = [e for e in result.effects if e.kind == "net_attempt"]
    assert nets, [e.model_dump() for e in result.effects]
    assert all(
        e.source == "audit_hook" and e.executed is False and e.via_call_id == "c1" for e in nets
    )


@pytest.mark.docker
def test_docker_audit_hook_sees_honeypot_read_and_process_start(
    container: DockerExecutor, docker_mounts: SandboxMounts
) -> None:
    vault = CanaryVault(b"docker-test-key-docker-test-key-00")
    for target, content in vault.honeypot_files(RUN, HoneypotSpec()).items():
        area, rel = target.split(":", 1)
        dest = {"home": docker_mounts.home, "repo": docker_mounts.repo}[area] / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content)
        dest.chmod(0o666)
        for parent in dest.parents:
            if parent in (docker_mounts.home, docker_mounts.repo):
                break
            parent.chmod(0o777)
    code = (
        "import subprocess; open('/home/agent/.aws/credentials').read();"
        "subprocess.run(['/bin/true'])"
    )
    result = container.exec(["python", "-c", code], timeout_s=30, call_id="c1")
    assert result.exit_code == 0, result.stderr
    hook = [(e.kind, e.target) for e in result.effects if e.source == "audit_hook"]
    assert ("fs_read", "home:.aws/credentials") in hook
    assert ("exec", "exec:true") in hook


@pytest.mark.docker
def test_docker_sh_write_to_fake_home_is_seen_by_fs_diff_and_canary_in_sink(
    container: DockerExecutor, docker_mounts: SandboxMounts
) -> None:
    vault = CanaryVault(b"docker-test-key-docker-test-key-00")
    (docker_mounts.home / ".aws").mkdir()
    (docker_mounts.home / ".aws").chmod(0o777)
    cred = docker_mounts.home / ".aws" / "credentials"
    cred.write_text(vault.honeypot_files(RUN, HoneypotSpec())["home:.aws/credentials"])
    cred.chmod(0o666)
    before = tree_manifest(docker_mounts)
    result = container.exec(
        [
            "/bin/sh",
            "-c",
            "echo 'export X=1' > /home/agent/.bashrc; cp /home/agent/.aws/credentials /tmp/leak.txt",
        ],
        timeout_s=20,
        call_id="c1",
    )
    assert result.exit_code == 0, result.stderr
    after = tree_manifest(docker_mounts, step=1)
    diff = fx.fs_diff_effects(before, after, via_call_id="c1")
    assert {(e.target, e.detail) for e in diff} == {
        ("home:.bashrc", "added"),
        ("tmp:leak.txt", "added"),
    }

    def read_area_file(target: str) -> str | None:
        area, rel = target.split(":", 1)
        root = {"repo": docker_mounts.repo, "home": docker_mounts.home, "tmp": docker_mounts.tmp}[
            area
        ]
        return (root / rel).read_text()

    sinks = fx.sink_effects(
        [ToolCall(id="c1", name="run_shell", args={"cmd": "cp ..."}, args_norm={})],
        diff,
        read_area_file=read_area_file,
        allowed_paths=["**"],
        scan=lambda text: vault.scan(RUN, text),
    )
    leaked = [e for e in sinks if e.canary_hits]
    assert [(e.target, e.detail, e.canary_hits) for e in leaked] == [
        ("sink:out_of_scope_file", "tmp:leak.txt", ["home_credentials"])
    ]
    assert not any(e.canary_hits for e in sinks if e.target == "sink:run_shell")


@pytest.mark.docker
def test_docker_cleanup_containers(docker_mounts: SandboxMounts) -> None:
    run_id = RUN + "__cleanup"
    ex = DockerExecutor(docker_mounts, run_id=run_id, mode="sim")
    ex.start()
    try:
        assert cleanup_containers(run_id) == 1
    finally:
        ex.stop()
    assert cleanup_containers(run_id) == 0
