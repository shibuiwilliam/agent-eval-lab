"""サンドボックスの実行器（SPEC.md 5.1・5.2・5.7・5.8 節、ADR-030）。

- `DockerExecutor`: run ごとの常駐コンテナ。境界は Docker 側（--network none、--read-only、非 root、書き込み可は 3 領域）
- `FakeExecutor`: テスト用。コードを実行せず、ホストのプロセスも起動しない

監査フック（sandbox_payload/sitecustomize.py）は計測器であって境界ではない。
"""

from __future__ import annotations

import atexit
import errno
import hashlib
import os
import posixpath
import re
import secrets
import stat
import subprocess
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import islice
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
from agenteval.env.effects import exec_log_effect, parse_hook_effects

PAYLOAD_DIR: Final = Path(__file__).parent / "sandbox_payload"

CONTAINER_NAME_PREFIX: Final = "agenteval-"
LABEL_RUN_ID: Final = "agenteval.run_id"
LABEL_MODE: Final = "agenteval.mode"
CONTAINER_SITECUSTOMIZE: Final = "/usr/local/lib/python3.13/site-packages/sitecustomize.py"
CONTAINER_MARKER_DIR: Final = (
    "/opt/agenteval/marker"  # EC-S1 の目印発生器の置き場（読み取り専用。12.4.3 節）
)
CONTAINER_PATH: Final = f"{CONTAINER_REPO}/.venv/bin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

KILL_GRACE_S: Final = 2  # coreutils timeout -k の猶予
HOST_TIMEOUT_MARGIN_S: Final = 10.0  # ホスト側の subprocess の timeout は timeout_s + この値
TIMEOUT_EXIT_CODE: Final = 124
DOCKER_CLI_ERROR_EXIT_CODE: Final = 125  # docker CLI 自身のエラー（デーモンの失敗など）
SPAWN_FAILURE_EXIT_CODE: Final = 127
HOOK_READ_LIMIT: Final = (
    16 * 1024 * 1024
)  # 1 回の回収で 1 ファイルから読む上限（エージェントが書き込める入力なので）

OUTPUT_LIMIT_BYTES: Final = (
    16 * 1024 * 1024
)  # docker CLI の stdout・stderr それぞれの上限（超えた分は捨てる）
FSIZE_LIMIT_BYTES: Final = (
    256 * 1024 * 1024
)  # コンテナ内のプロセスが作れる 1 ファイルの上限（--ulimit fsize）
OUTPUT_TRUNCATED_NOTE: Final = "\n[… agenteval: {which} が {limit} バイトを超えたため打ち切った …]"
REPORT_NAME: Final = re.compile(r"^[0-9]+\.xml$")  # reports 領域に作ってよい名前（<n>.xml）
REPORT_SCAN_LIMIT: Final = 10_000  # reports 領域の走査で数える項目の上限
REPORT_MAX_DEPTH: Final = (
    32  # reports 領域の走査で降りる深さの上限（これより深いディレクトリは名前だけ記録する）
)
REPORT_SCAN_TRUNCATED_DETAIL: Final = "reports_scan_truncated"
REPORT_TOO_DEEP_DETAIL: Final = "reports_too_deep"
EXEC_TOKEN_ENV: Final = "AGENTEVAL_EXEC_TOKEN"
# PID 1。シグナルを無視して子を回収するだけのループ。名前空間の内側からは SIGKILL もできないので、
# エージェントが `pkill sleep` や `kill -9 -1` を実行してもコンテナは止まらない（--init の tini は殺せてしまう）
PID1_SCRIPT: Final = 'trap "" TERM INT HUP; while :; do wait; sleep 1; done'
# docker exec の中で動く外側のスクリプト。`$1` が秒数、残りが実行するコマンド。コマンドが終わったら、
# この exec のトークンを環境に持つプロセス（背景に残した子・setsid した子を含む）を殺してから終了する。
# こうしないと、背景の子が出力パイプを握ったままになり docker exec が 2 秒居座り、子も次の呼び出し以降に生き残る
EXEC_WRAPPER: Final = (
    't=$1; shift; timeout -k 2 "$t" "$@"; rc=$?; '
    f'pids=$(grep -la "{EXEC_TOKEN_ENV}=${EXEC_TOKEN_ENV}" /proc/[0-9]*/environ 2>/dev/null '
    + r"| sed -n 's#^/proc/\([0-9]*\)/environ$#\1#p'); "
    + 'for p in $pids; do [ "$p" != "$$" ] && kill -9 "$p" 2>/dev/null; done; exit $rc'
)
PYTHON_ARGV0: Final = re.compile(r"^(python[0-9.]*|pytest|py\.test)$")
HOOK_START_DETAIL: Final = "hook.start"
HOOK_MISSING_DETAIL: Final = "hook_missing"
HOOK_LOG_IRREGULAR_DETAIL: Final = "hook_log_not_regular"

DockerRunner = Callable[..., "subprocess.CompletedProcess[str]"]


@dataclass
class _ReportsScan:
    """reports 領域の走査結果。"""

    seen: dict[str, tuple[int, int, int]] = field(default_factory=dict)
    too_deep: list[str] = field(default_factory=list)
    truncated: bool = False


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
    fsize_bytes: int = FSIZE_LIMIT_BYTES  # --ulimit fsize（作業ツリー等に書ける 1 ファイルの上限）
    # EC-S1 の目印発生器の置き場（SPEC 12.4.3 節）。None（既定）ならマウントしない。
    # 与えると CONTAINER_MARKER_DIR に ro で 1 本だけ足す（書き込み可の領域は増えない）
    marker_dir: Path | None = None


def _stream_run(
    argv: Sequence[str], *, timeout: float, limit: int = OUTPUT_LIMIT_BYTES
) -> subprocess.CompletedProcess[str]:
    """docker CLI を実行し、stdout・stderr をストリームで読む。それぞれ limit バイトを超えた分は捨てる。

    出力が上限を超えたら、stderr の末尾にその旨を注記する（ExecResult に旗の欄が無いため）。
    捨てるだけで読み続けるので、子プロセスが書き込みで詰まることは無い。時間切れなら子を kill して
    subprocess.TimeoutExpired を投げる。
    """
    proc = subprocess.Popen(
        list(argv), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    assert proc.stdout is not None and proc.stderr is not None
    buffers = (bytearray(), bytearray())
    truncated = [False, False]

    def pump(fd: int, index: int) -> None:
        buf = buffers[index]
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                return
            room = limit - len(buf)
            if room > 0:
                buf += chunk[:room]
            if len(chunk) > max(room, 0):
                truncated[index] = True

    threads = [
        threading.Thread(target=pump, args=(proc.stdout.fileno(), 0), daemon=True),
        threading.Thread(target=pump, args=(proc.stderr.fileno(), 1), daemon=True),
    ]
    for thread in threads:
        thread.start()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        raise
    finally:
        for thread in threads:
            thread.join(timeout=5)
        proc.stdout.close()
        proc.stderr.close()
    out = bytes(buffers[0]).decode("utf-8", "replace")
    err = bytes(buffers[1]).decode("utf-8", "replace")
    for flag, which in zip(truncated, ("stdout", "stderr"), strict=True):
        if flag:
            err += OUTPUT_TRUNCATED_NOTE.format(which=which, limit=limit)
    return subprocess.CompletedProcess(list(argv), proc.returncode, out, err)


def _run_docker(
    argv: Sequence[str], *, timeout: float, runner: DockerRunner | None = None
) -> subprocess.CompletedProcess[str]:
    """docker CLI を実行する。runner はテストが差し込む（実際には起動しない）。"""
    if runner is None:
        return _stream_run(argv, timeout=timeout)
    return runner(
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


def _check_argv(argv: Sequence[str]) -> None:
    """argv が空なら ValueError（何を実行するのか決まらない）。"""
    if not argv:
        raise ValueError("argv が空")


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
        self._irregular_logs: set[Path] = set()
        self._reports_seen: dict[str, tuple[int, int, int]] = {}
        self._reports_deep_seen: set[str] = set()
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
        if cfg.marker_dir is not None:
            volumes.append((cfg.marker_dir, CONTAINER_MARKER_DIR, "ro"))
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
            "--memory-swap",
            cfg.memory,
            "--ulimit",
            f"fsize={cfg.fsize_bytes}:{cfg.fsize_bytes}",
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
        argv += ["-w", CONTAINER_REPO, cfg.image, "sh", "-c", PID1_SCRIPT]
        return argv

    def build_exec_argv(
        self,
        argv: Sequence[str],
        *,
        timeout_s: float,
        cwd: str = ".",
        call_id: str | None = None,
        env: Mapping[str, str] | None = None,
        token: str | None = None,
    ) -> list[str]:
        """`docker exec ...` の引数。cwd が不正、argv が空なら ValueError。

        コマンドは外側のスクリプト EXEC_WRAPPER（`sh -c`）の中で `timeout -k 2 <s> <argv...>` として動く。
        token はこの exec のプロセスを見分ける印（省略すると乱数）。
        """
        _check_argv(argv)
        rel = _check_cwd(cwd)
        workdir = CONTAINER_REPO if rel == "." else f"{CONTAINER_REPO}/{rel}"
        out = [self._config.docker_bin, "exec"]
        out += ["-e", f"{EXEC_TOKEN_ENV}={token or secrets.token_hex(8)}"]
        if call_id is not None:
            out += ["-e", f"{CALL_ID_ENV}={call_id}"]
        for key, value in (env or {}).items():
            out += ["-e", f"{key}={value}"]
        out += ["-w", workdir, self.container_name]
        out += ["sh", "-c", EXEC_WRAPPER, "agenteval-exec", f"{timeout_s:g}", *argv]
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
        if self._config.marker_dir is not None and not self._config.marker_dir.is_dir():
            raise SandboxError(f"目印発生器のディレクトリが無い: {self._config.marker_dir}")
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
        self._reports_seen = self._scan_reports().seen
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
        _check_argv(argv)
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
        hook_effects = self._collect_hook_effects(call_id)
        effects += hook_effects
        effects += self._reports_effects(call_id)
        # python を起動したのにフックの起動記録が 1 件も無い = 「検出されなかった」ではなく「観測できなかった」
        # （python -S、仮想環境の sitecustomize による遮蔽、フックの沈黙など）
        if (
            PYTHON_ARGV0.match(posixpath.basename(argv[0]))
            and exit_code != SPAWN_FAILURE_EXIT_CODE
            and not any(e.detail.startswith(HOOK_START_DETAIL) for e in hook_effects)
        ):
            effects.append(
                exec_log_effect(argv, call_id=call_id).model_copy(
                    update={"detail": HOOK_MISSING_DETAIL}
                )
            )
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

    def _is_docker_failure(self, proc: subprocess.CompletedProcess[str]) -> bool:
        """docker CLI 自身の失敗（コンテナが落ちている等）か。コマンドの非 0 終了とは区別する。

        エージェントのコマンドも 125 で終われる・同じ文字列を出せるので、どちらか 1 つだけでは決めない。
        終了コード 125 と daemon のエラー文字列が揃えば失敗、どちらか片方なら docker inspect でコンテナの状態を確かめる。
        """
        if proc.returncode == 0:
            return False
        has_prefix = proc.stderr.startswith(
            ("Error response from daemon:", "Error: No such container")
        )
        is_125 = proc.returncode == DOCKER_CLI_ERROR_EXIT_CODE
        if is_125 and has_prefix:
            return True
        if not (is_125 or has_prefix):
            return False
        state = self._docker(
            ["inspect", "-f", "{{.State.Running}}", self.container_name], timeout=30
        )
        return state.returncode != 0 or state.stdout.strip() != "true"

    def _read_hook_log(self, path: Path, call_id: str | None) -> tuple[bytes, Effect | None]:
        """効果ログ 1 ファイルの未読分を読む。通常ファイル以外は読まず、1 回だけ効果として記録する。

        effects ディレクトリはエージェントが書き込めるので、FIFO・シンボリックリンク・ディレクトリを置かれても
        ホストが固まらないように O_NONBLOCK | O_NOFOLLOW で開き、fstat で通常ファイルを確かめる
        （is_symlink してから open する TOCTOU も塞ぐ）。
        """
        irregular = Effect(
            kind="fs_write",
            target=f"abs:{CONTAINER_EFFECTS}/{path.name}",
            source="fs_diff",
            via_call_id=call_id,
            detail=HOOK_LOG_IRREGULAR_DETAIL,
        )
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        except OSError as exc:
            if exc.errno == errno.ELOOP and path not in self._irregular_logs:
                self._irregular_logs.add(path)
                return b"", irregular
            return b"", None
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                if path in self._irregular_logs:
                    return b"", None
                self._irregular_logs.add(path)
                return b"", irregular
            return os.pread(fd, HOOK_READ_LIMIT, self._offsets.get(path, 0)), None
        except OSError:
            return b"", None
        finally:
            os.close(fd)

    def _collect_hook_effects(self, call_id: str | None) -> list[Effect]:
        """ホスト側の mounts.effects の *.jsonl を、前回の読み取り位置から末尾の完全な行まで読む。

        読み取り位置はファイルごとに持つ。exec の後も生き残ったプロセスの記録は次の回収で拾われる。
        エージェントが書き込める入力なので、通常ファイル以外は読まず（_read_hook_log）、
        1 回の読み取りに上限を置く。改行の無いまま上限を超えた行は、その分を捨てて読み取り位置を進める。
        """
        step_call_ids = {call_id} if call_id is not None else set()
        effects: list[Effect] = []
        try:
            paths = sorted(self._mounts.effects.glob("*.jsonl"))
        except OSError:
            return effects
        for path in paths:
            data, anomaly = self._read_hook_log(path, call_id)
            if anomaly is not None:
                effects.append(anomaly)
            end = data.rfind(b"\n")
            if end < 0:
                if len(data) >= HOOK_READ_LIMIT:
                    self._offsets[path] = self._offsets.get(path, 0) + len(data)
                continue
            self._offsets[path] = self._offsets.get(path, 0) + end + 1
            for raw in data[: end + 1].splitlines():
                effects += parse_hook_effects(
                    raw.decode("utf-8", "replace"),
                    current_call_id=call_id,
                    step_call_ids=step_call_ids,
                )
        return effects

    def _scan_reports(self) -> _ReportsScan:
        """reports 領域を明示スタックで走査する（深い入れ子で再帰が溢れない）。

        項目は (種別, サイズ, mtime_ns)。想定した名前（最上位の <n>.xml の通常ファイル）は含めない。
        シンボリックリンクは辿らない。深さが REPORT_MAX_DEPTH を超えるディレクトリは中に入らず名前だけ記録し、
        項目数が REPORT_SCAN_LIMIT に達したら打ち切る。どちらも truncated を立てる。
        """
        scan = _ReportsScan()
        stack: list[tuple[str, str, int]] = [(str(self._mounts.reports), "", 0)]
        while stack:
            directory, prefix, depth = stack.pop()
            try:
                with os.scandir(directory) as it:
                    # 巨大なディレクトリでメモリを使い切らないよう、上限を超える分は読まない
                    entries = sorted(islice(it, REPORT_SCAN_LIMIT + 1), key=lambda e: e.name)
            except OSError:
                continue
            for entry in entries:
                if len(scan.seen) >= REPORT_SCAN_LIMIT:
                    scan.truncated = True
                    return scan
                try:
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                rel = prefix + entry.name
                kind = stat.S_IFMT(st.st_mode)
                if not (not prefix and kind == stat.S_IFREG and REPORT_NAME.match(entry.name)):
                    scan.seen[rel] = (kind, st.st_size, st.st_mtime_ns)
                if stat.S_ISDIR(st.st_mode):
                    if depth + 1 > REPORT_MAX_DEPTH:
                        scan.too_deep.append(rel)
                        scan.truncated = True
                    else:
                        stack.append((entry.path, rel + "/", depth + 1))
        return scan

    def _reports_effects(self, call_id: str | None) -> list[Effect]:
        """reports 領域への、想定外の名前の作成・変更・削除を範囲外の効果（fs_diff）にする。

        reports（/var/agenteval/reports）は pytest の結果ファイル <n>.xml 用に rw でマウントしている。
        effects ディレクトリは観測の外（監査フックのログの置き場。盲点として SPEC に書く）。
        走査が上限で打ち切られた回は、一部しか見えていないので added / deleted を出さず（走査順による誤判定を避ける）、
        reports_scan_truncated を 1 件残す。深すぎるディレクトリは名前だけ reports_too_deep で記録する。
        """
        now = self._scan_reports()
        before = self._reports_seen
        out: list[Effect] = []

        def effect(rel: str, detail: str) -> Effect:
            target = f"abs:{CONTAINER_REPORTS}/{rel}" if rel else f"abs:{CONTAINER_REPORTS}"
            return Effect(
                kind="fs_write", target=target, source="fs_diff", via_call_id=call_id, detail=detail
            )

        for rel in now.too_deep:
            if rel not in self._reports_deep_seen:
                self._reports_deep_seen.add(rel)
                out.append(effect(rel, REPORT_TOO_DEEP_DETAIL))
        for rel in sorted(before.keys() | now.seen.keys()):
            old, new = before.get(rel), now.seen.get(rel)
            if old == new:
                continue
            if old is None:
                change = "added"
            elif new is None:
                change = "deleted"
            else:
                change = "modified"
            if now.truncated and change != "modified":
                continue
            out.append(effect(rel, change))
        if now.truncated:
            out.append(effect("", REPORT_SCAN_TRUNCATED_DETAIL))
            # 部分的な結果で基準を置き換えると、次の完全な走査で誤った added / deleted が出る。見えた分だけ更新する
            self._reports_seen = {**before, **now.seen}
        else:
            self._reports_seen = now.seen
        return out


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
        _check_argv(argv)
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
