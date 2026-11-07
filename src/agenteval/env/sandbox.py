"""サンドボックスの実行器（SPEC.md 5.1・5.2・5.7・5.8 節、ADR-030）。

- `DockerExecutor`: run ごとの常駐コンテナ。境界は Docker 側（--network none、--read-only、非 root、書き込み可は 3 領域）
- `FakeExecutor`: テスト用。コードを実行せず、ホストのプロセスも起動しない

監査フック（sandbox_payload/sitecustomize.py）は計測器であって境界ではない。
"""

from __future__ import annotations

import atexit
import hashlib
import os
import posixpath
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Final, Self

from pydantic import BaseModel

from agenteval.core.coding_types import (
    CALL_ID_ENV,
    CONTAINER_EFFECTS,
    CONTAINER_HOME,
    CONTAINER_REPO,
    CONTAINER_REPORTS,
    CONTAINER_TMP,
    EFFECT_LOG_ENV,
    ExecResult,
    Executor,
    ExecutorFactory,
    ExecutorKind,
    ModeDir,
    SandboxMounts,
)
from agenteval.core.schema import Effect
from agenteval.env.effects import exec_log_effect, parse_hook_record

PAYLOAD_DIR: Final = Path(__file__).parent / "sandbox_payload"

CONTAINER_NAME_PREFIX: Final = "agenteval-"
LABEL_RUN_ID: Final = "agenteval.run_id"
LABEL_MODE: Final = "agenteval.mode"
CONTAINER_SITECUSTOMIZE: Final = "/usr/local/lib/python3.13/site-packages/sitecustomize.py"
CONTAINER_PATH: Final = f"{CONTAINER_REPO}/.venv/bin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

KILL_GRACE_S: Final = 2  # coreutils timeout -k の猶予
HOST_TIMEOUT_MARGIN_S: Final = 10.0  # ホスト側の subprocess の timeout は timeout_s + この値
TIMEOUT_EXIT_CODE: Final = 124
SPAWN_FAILURE_EXIT_CODE: Final = 127
HOOK_READ_LIMIT: Final = (
    16 * 1024 * 1024
)  # 1 回の回収で 1 ファイルから読む上限（エージェントが書き込める入力なので）

DockerRunner = Callable[..., "subprocess.CompletedProcess[str]"]


class SandboxError(RuntimeError):
    """サンドボックスの失敗（コンテナの起動失敗、ホスト側の時間切れなど）。run は失敗として終える。"""


@dataclass(frozen=True)
class DockerConfig:
    """コンテナの構成の既定値（SPEC 5.2 節）。"""

    image: str = "agenteval-sandbox:py3.13"
    cpus: float = 1.0
    memory: str = "1g"
    pids_limit: int = 256
    user: str = "1000:1000"
    payload_dir: Path = PAYLOAD_DIR  # EC-O2 の植込みは別のディレクトリを渡せる
    docker_bin: str = "docker"


def _run_docker(
    argv: Sequence[str], *, timeout: float, runner: DockerRunner | None = None
) -> subprocess.CompletedProcess[str]:
    """docker CLI を実行する。runner はテストが差し込む（実際には起動しない）。"""
    run = runner if runner is not None else subprocess.run
    return run(
        list(argv),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def docker_available(config: DockerConfig | None = None) -> bool:
    """Docker デーモンに到達でき、サンドボックスのイメージがビルド済みか（conftest の判定と同じ）。

    AGENTEVAL_SKIP_DOCKER=1 なら False。
    """
    cfg = config or DockerConfig()
    if os.environ.get("AGENTEVAL_SKIP_DOCKER") == "1":
        return False
    try:
        proc = _run_docker([cfg.docker_bin, "image", "inspect", cfg.image], timeout=15)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def _check_cwd(cwd: str) -> str:
    """cwd を作業ツリー相対の正規形にする。絶対パスと `..` を含むものは ValueError。"""
    if cwd.startswith("/") or ".." in cwd.split("/"):
        raise ValueError(f"cwd は作業ツリー相対で、'..' を含んではいけない: {cwd!r}")
    return posixpath.normpath(cwd or ".")


class DockerExecutor:
    """run ごとに 1 つ常駐させるコンテナと `docker exec`（SPEC 5.2 節）。"""

    def __init__(
        self,
        mounts: SandboxMounts,
        *,
        run_id: str,
        mode: ModeDir,
        config: DockerConfig | None = None,
        runner: DockerRunner | None = None,
    ) -> None:
        self._mounts = mounts
        self._run_id = run_id
        self._mode = mode
        self._config = config or DockerConfig()
        self._runner = runner
        self._state: str = "new"  # new -> running -> stopped
        self._offsets: dict[Path, int] = {}
        self.container_name = (
            CONTAINER_NAME_PREFIX + hashlib.sha256((mode + run_id).encode()).hexdigest()[:16]
        )

    # --- Executor Protocol ---------------------------------------------------
    @property
    def kind(self) -> ExecutorKind:
        return "docker"

    @property
    def network_isolated(self) -> bool:
        return True

    @property
    def mounts(self) -> SandboxMounts:
        return self._mounts

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.stop()

    # --- docker の引数（テストが起動せずに検査する） ---------------------------
    def build_run_argv(self) -> list[str]:
        """`docker run -d ...` の引数（SPEC 5.2 節のとおり）。"""
        cfg, m = self._config, self._mounts
        volumes = [
            (m.repo, CONTAINER_REPO, "rw"),
            (m.home, CONTAINER_HOME, "rw"),
            (m.tmp, CONTAINER_TMP, "rw"),
            (m.effects, CONTAINER_EFFECTS, "rw"),
            (m.reports, CONTAINER_REPORTS, "rw"),
            (cfg.payload_dir / "sitecustomize.py", CONTAINER_SITECUSTOMIZE, "ro"),
        ]
        argv = [
            cfg.docker_bin,
            "run",
            "-d",
            "--name",
            self.container_name,
            "--label",
            f"{LABEL_RUN_ID}={self._run_id}",
            "--label",
            f"{LABEL_MODE}={self._mode}",
            "--network",
            "none",
            "--read-only",
            "--user",
            cfg.user,
            "--cpus",
            f"{cfg.cpus:g}",
            "--memory",
            cfg.memory,
            "--pids-limit",
            str(cfg.pids_limit),
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
        ]
        for host, container, flag in volumes:
            host_path = str(host.resolve())
            if ":" in host_path or "," in host_path:
                raise SandboxError(f"-v に渡せないパス（':' か ',' を含む）: {host_path}")
            argv += ["-v", f"{host_path}:{container}:{flag}"]
        env = {
            "HOME": CONTAINER_HOME,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PATH": CONTAINER_PATH,
            "VIRTUAL_ENV": f"{CONTAINER_REPO}/.venv",
            "PIP_NO_CACHE_DIR": "1",
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PIP_NO_INPUT": "1",
            EFFECT_LOG_ENV: CONTAINER_EFFECTS,
        }
        for key, value in env.items():
            argv += ["-e", f"{key}={value}"]
        argv += ["-w", CONTAINER_REPO, cfg.image, "sleep", "infinity"]
        return argv

    def build_exec_argv(
        self,
        argv: Sequence[str],
        *,
        timeout_s: float,
        cwd: str = ".",
        call_id: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> list[str]:
        """`docker exec ...` の引数。cwd が不正なら ValueError。"""
        rel = _check_cwd(cwd)
        workdir = CONTAINER_REPO if rel == "." else f"{CONTAINER_REPO}/{rel}"
        out = [self._config.docker_bin, "exec"]
        if call_id is not None:
            out += ["-e", f"{CALL_ID_ENV}={call_id}"]
        for key, value in (env or {}).items():
            out += ["-e", f"{key}={value}"]
        out += ["-w", workdir, self.container_name]
        out += ["timeout", "-k", str(KILL_GRACE_S), f"{timeout_s:g}", *argv]
        return out

    # --- ライフサイクル --------------------------------------------------------
    def start(self) -> None:
        """コンテナを起動する。冪等。stop 後の再開始は RuntimeError。"""
        if self._state == "running":
            return
        if self._state == "stopped":
            raise RuntimeError("stop 済みの DockerExecutor は再開始できない")
        if not (self._config.payload_dir / "sitecustomize.py").is_file():
            raise SandboxError(f"監査フックが無い: {self._config.payload_dir}/sitecustomize.py")
        for directory in (
            self._mounts.repo,
            self._mounts.home,
            self._mounts.tmp,
            self._mounts.effects,
            self._mounts.reports,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        run_argv = self.build_run_argv()
        # 同名の取り残し（前の run が異常終了した跡）を消してから起動する
        self._docker(["rm", "-f", self.container_name], timeout=60)
        proc = self._docker_argv(run_argv, timeout=120)
        if proc.returncode != 0:
            raise SandboxError(f"docker run が失敗した: {proc.stderr.strip()}")
        self._state = "running"
        atexit.register(self.stop)

    def stop(self) -> None:
        """コンテナを消す（docker rm -f）。冪等。"""
        if self._state != "running":
            return
        self._state = "stopped"
        atexit.unregister(self.stop)
        self._docker(["rm", "-f", self.container_name], timeout=60)

    def exec(
        self,
        argv: Sequence[str],
        *,
        timeout_s: float,
        cwd: str = ".",
        call_id: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> ExecResult:
        """作業ツリー相対の cwd で argv を実行する。時間はコンテナ内 timeout とホスト側の二重で切る。"""
        if self._state != "running":
            raise RuntimeError(f"DockerExecutor が実行中でない（state={self._state}）")
        docker_argv = self.build_exec_argv(
            argv, timeout_s=timeout_s, cwd=cwd, call_id=call_id, env=env
        )
        started = time.monotonic()
        try:
            proc = self._docker_argv(docker_argv, timeout=timeout_s + HOST_TIMEOUT_MARGIN_S)
        except subprocess.TimeoutExpired as exc:
            self._docker(["kill", self.container_name], timeout=30)
            raise SandboxError(
                f"docker exec がホスト側の時間切れ（{timeout_s:g}s + 余裕）"
            ) from exc
        duration_ms = int((time.monotonic() - started) * 1000)
        if self._is_docker_failure(proc):
            raise SandboxError(f"docker exec が失敗した: {proc.stderr.strip()}")
        exit_code = proc.returncode
        # coreutils timeout は 124。TERM に応じず KILL まで進むと 137（OOM と区別するため経過時間も見る）
        timed_out = exit_code == TIMEOUT_EXIT_CODE or (
            exit_code == 137 and duration_ms >= timeout_s * 1000
        )
        if timed_out:
            exit_code = TIMEOUT_EXIT_CODE
        effects: list[Effect] = [exec_log_effect(argv, call_id=call_id)]
        effects += self._collect_hook_effects(call_id)
        return ExecResult(
            argv=list(argv),
            cwd=cwd,
            exit_code=exit_code,
            stdout=proc.stdout,
            stderr=proc.stderr,
            duration_ms=duration_ms,
            timed_out=timed_out,
            effects=effects,
        )

    # --- 内部 ------------------------------------------------------------------
    def _docker_argv(
        self, argv: Sequence[str], *, timeout: float
    ) -> subprocess.CompletedProcess[str]:
        return _run_docker(argv, timeout=timeout, runner=self._runner)

    def _docker(self, args: Sequence[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        """後始末用の docker サブコマンド。失敗しても例外にしない。"""
        try:
            return self._docker_argv([self._config.docker_bin, *args], timeout=timeout)
        except (OSError, subprocess.TimeoutExpired):
            return subprocess.CompletedProcess(list(args), 1, "", "")

    @staticmethod
    def _is_docker_failure(proc: subprocess.CompletedProcess[str]) -> bool:
        """docker CLI 自身の失敗（コンテナが無い等）か。コマンドの非 0 終了とは区別する。"""
        return proc.returncode != 0 and proc.stderr.startswith(
            ("Error response from daemon:", "Error: No such container")
        )

    def _collect_hook_effects(self, call_id: str | None) -> list[Effect]:
        """ホスト側の mounts.effects の *.jsonl を、前回の読み取り位置から末尾の完全な行まで読む。

        読み取り位置はファイルごとに持つ。exec の後も生き残ったプロセスの記録は次の回収で拾われる。
        エージェントが書き込める入力なので、シンボリックリンクは辿らず、1 回の読み取りに上限を置く。
        """
        step_call_ids = {call_id} if call_id is not None else set()
        effects: list[Effect] = []
        try:
            paths = sorted(self._mounts.effects.glob("*.jsonl"))
        except OSError:
            return effects
        for path in paths:
            if path.is_symlink():
                continue
            offset = self._offsets.get(path, 0)
            try:
                with path.open("rb") as fh:
                    fh.seek(offset)
                    data = fh.read(HOOK_READ_LIMIT)
            except OSError:
                continue
            end = data.rfind(b"\n")
            if end < 0:
                continue
            self._offsets[path] = offset + end + 1
            for raw in data[: end + 1].splitlines():
                effect = parse_hook_record(
                    raw.decode("utf-8", "replace"),
                    current_call_id=call_id,
                    step_call_ids=step_call_ids,
                )
                if effect is not None:
                    effects.append(effect)
        return effects


# --- FakeExecutor（5.7 節） ---------------------------------------------------
class FakeCall(BaseModel):
    """FakeExecutor が受けた exec 1 回分。"""

    argv: list[str]
    cwd: str
    timeout_s: float
    call_id: str | None
    env: dict[str, str]


FakeResponder = Callable[[FakeCall, SandboxMounts], ExecResult]


class FakeExecutor:
    """コードを実行しない Executor。kind="fake"、network_isolated=False。

    responder が None なら exit_code 0・空出力・exec_log の効果 1 件を返す。
    responder があれば、その戻り値を返す（exec_log の効果が無ければ 1 件足す。DockerExecutor と同じく毎 exec で 1 件）。
    テストは responder の中で mounts のディレクトリに直接ファイルを書いて、エージェントの書き込みを模してよい。
    呼ばれた順に calls に記録する。
    """

    calls: list[FakeCall]

    def __init__(self, mounts: SandboxMounts, *, responder: FakeResponder | None = None) -> None:
        self._mounts = mounts
        self._responder = responder
        self._state = "new"
        self.calls = []

    @property
    def kind(self) -> ExecutorKind:
        return "fake"

    @property
    def network_isolated(self) -> bool:
        return False

    @property
    def mounts(self) -> SandboxMounts:
        return self._mounts

    def start(self) -> None:
        """冪等。stop 後の再開始は RuntimeError（DockerExecutor と同じ）。"""
        if self._state == "stopped":
            raise RuntimeError("stop 済みの FakeExecutor は再開始できない")
        self._state = "running"

    def stop(self) -> None:
        """冪等。"""
        if self._state == "running":
            self._state = "stopped"

    def exec(
        self,
        argv: Sequence[str],
        *,
        timeout_s: float,
        cwd: str = ".",
        call_id: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> ExecResult:
        """呼び出しを記録し、responder の結果（または既定の結果）を返す。"""
        if self._state != "running":
            raise RuntimeError(f"FakeExecutor が実行中でない（state={self._state}）")
        _check_cwd(cwd)
        call = FakeCall(
            argv=list(argv), cwd=cwd, timeout_s=timeout_s, call_id=call_id, env=dict(env or {})
        )
        self.calls.append(call)
        if self._responder is None:
            result = ExecResult(
                argv=list(argv), cwd=cwd, exit_code=0, stdout="", stderr="", duration_ms=0
            )
        else:
            result = self._responder(call, self._mounts)
        if not any(e.source == "exec_log" for e in result.effects):
            result = result.model_copy(
                update={"effects": [exec_log_effect(argv, call_id=call_id), *result.effects]}
            )
        return result


# --- ファクトリと後始末（5.8 節） ----------------------------------------------
def docker_executor_factory(
    run_id: str, mode: ModeDir, config: DockerConfig | None = None
) -> ExecutorFactory:
    """RepoEnv.create に渡す Executor のファクトリ（マウントを受けて DockerExecutor を作る）。"""

    def factory(mounts: SandboxMounts) -> Executor:
        return DockerExecutor(mounts, run_id=run_id, mode=mode, config=config)

    return factory


def fake_executor_factory(responder: FakeResponder | None = None) -> ExecutorFactory:
    """テスト用のファクトリ（FakeExecutor を作る）。"""

    def factory(mounts: SandboxMounts) -> Executor:
        return FakeExecutor(mounts, responder=responder)

    return factory


def cleanup_containers(
    run_id: str | None = None,
    *,
    docker_bin: str = "docker",
    runner: DockerRunner | None = None,
) -> int:
    """ラベル agenteval.run_id の付いたコンテナを消す（runner が波の前後に使う）。消した数を返す。

    run_id が None なら全 run のコンテナ。docker に到達できなければ 0。
    """
    label = LABEL_RUN_ID if run_id is None else f"{LABEL_RUN_ID}={run_id}"
    try:
        listing = _run_docker(
            [docker_bin, "ps", "-aq", "--filter", f"label={label}"], timeout=30, runner=runner
        )
        ids = listing.stdout.split() if listing.returncode == 0 else []
        if ids:
            _run_docker([docker_bin, "rm", "-f", *ids], timeout=120, runner=runner)
    except (OSError, subprocess.TimeoutExpired):
        return 0
    return len(ids)
