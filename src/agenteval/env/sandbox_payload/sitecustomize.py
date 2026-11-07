"""サンドボックスの監査フック（計測器。境界ではない。SPEC.md 5.4 節、ADR-030 判断 3・5）。

コンテナ内の Python がすべて読み込む（システムの site-packages に実行時に読み取り専用でマウントする）。
標準ライブラリだけを使い、agenteval を import しない。どんな例外もホストのプログラムに伝えない。

- 環境変数 AGENTEVAL_EFFECT_LOG_DIR が無ければ何もしない
- 起動時に `<dir>/<pid>-<monotonic_ns>.jsonl` を開き、hook.start を 1 行書いて sys.addaudithook を登録する
- 対象のイベントだけを 1 行 1 JSON で書く。書き込みは開いた fd への os.write だけ（open を使うと監査が再帰する）
- 書き込みに失敗したら以後は黙って止める
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Sequence
from typing import Any

LOG_DIR_ENV = "AGENTEVAL_EFFECT_LOG_DIR"
CALL_ID_ENV = "AGENTEVAL_CALL_ID"
MAX_STR = 300  # 文字列の引数はこの長さで切る
MAX_ARGV = 32  # argv は先頭のこの個数だけ記録する

# 記録しないパス（雑音）。SPEC 5.4 節
NOISE_PREFIXES = (
    "/usr/local/lib/python3",  # python3.13/ と python313.zip
    "/usr/lib/",
    "/lib/",
    "/proc/",
    "/sys/",
    "/dev/",
    "/etc/ld.so",
    "/var/agenteval/",
)
# 読み取りだけを雑音として捨てる（書き込みは F3 系統の観測に要るので記録する）
READ_NOISE_PREFIXES = ("/work/repo/.venv/lib/",)

EVENTS_DELETE = ("os.remove", "os.rmdir", "shutil.rmtree")


def trunc(value: object) -> str:
    """任意の値を文字列にして MAX_STR 文字で切る。"""
    if isinstance(value, bytes):
        text = os.fsdecode(value)
    else:
        text = str(value)
    return text if len(text) <= MAX_STR else text[:MAX_STR]


def absolute(path: object) -> str | None:
    """パス（str / bytes / PathLike）を cwd 基準で絶対化する。fd（int）など扱えないものは None。"""
    if isinstance(path, int) or path is None:
        return None
    try:
        return os.path.abspath(os.fsdecode(path))  # type: ignore[arg-type]
    except Exception:  # noqa: BLE001 - 計測器はホストのプログラムに例外を伝えない
        return None


def is_noise(path: str, *, write: bool) -> bool:
    """雑音のパスか。"""
    if "__pycache__" in path:
        return True
    if path.startswith(NOISE_PREFIXES):
        return True
    return (not write) and path.startswith(READ_NOISE_PREFIXES)


def is_write_open(mode: object, flags: object) -> bool:
    """open の mode 文字列と flags から書き込み系かを判定する（コンテナ内の OS の定数で）。"""
    if isinstance(mode, str) and any(ch in mode for ch in "wax+"):
        return True
    if isinstance(flags, int):
        mask = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC
        return bool(flags & mask)
    return False


def argv_list(args: object) -> list[str]:
    """argv らしい値を文字列のリストにする。"""
    if isinstance(args, (str, bytes)):
        return [trunc(args)]
    if isinstance(args, Sequence):
        return [trunc(a) for a in list(args)[:MAX_ARGV]]
    return []


class Recorder:
    """監査イベントを JSON 行にして fd に書く。テストは sys.addaudithook を使わず handle を直接呼ぶ。"""

    def __init__(self, fd: int) -> None:
        self.fd = fd
        self.dead = False
        self.busy = False

    def handle(self, event: str, args: tuple[Any, ...]) -> None:
        """sys.addaudithook に登録する関数。例外は外に出さない。"""
        if self.dead or self.busy:
            return
        self.busy = True
        try:
            record = self.build(event, args)
            if record is not None:
                self.write(record)
        except Exception:  # noqa: BLE001, S110 - 計測器はホストのプログラムに例外を伝えない
            pass
        finally:
            self.busy = False

    def write(self, record: dict[str, Any]) -> None:
        """1 行を書く。失敗したら以後は黙って止める。"""
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        try:
            os.write(self.fd, line.encode("utf-8", "replace"))
        except Exception:  # noqa: BLE001 - 書き込みに失敗したら以後は黙って止める
            self.dead = True

    def envelope(self, event: str, args: dict[str, Any]) -> dict[str, Any]:
        """共通の枠（v・pid・ppid・call_id・event・args）。"""
        return {
            "v": 1,
            "pid": os.getpid(),
            "ppid": os.getppid(),
            "call_id": os.environ.get(CALL_ID_ENV),
            "event": event,
            "args": args,
        }

    def build(self, event: str, args: tuple[Any, ...]) -> dict[str, Any] | None:
        """対象のイベントだけを記録の辞書にする。対象外・雑音は None。"""
        if event == "open":
            path = absolute(args[0])
            if path is None:
                return None
            mode = args[1] if len(args) > 1 else None
            flags = args[2] if len(args) > 2 else None
            write = is_write_open(mode, flags)
            if is_noise(path, write=write):
                return None
            return self.envelope(
                "open",
                {
                    "path": trunc(path),
                    "mode": None if mode is None else trunc(mode),
                    "flags": flags if isinstance(flags, int) else None,
                    # 判定はコンテナ内の OS の定数で行う（ホストの定数とは値が違うため、結果を持たせる）
                    "write": write,
                },
            )
        if event in EVENTS_DELETE:
            path = absolute(args[0])
            if path is None or is_noise(path, write=True):
                return None
            return self.envelope(event, {"path": trunc(path)})
        if event == "os.rename":
            src, dst = absolute(args[0]), absolute(args[1])
            if src is None or dst is None:
                return None
            if is_noise(src, write=True) and is_noise(dst, write=True):
                return None
            return self.envelope(event, {"src": trunc(src), "dst": trunc(dst)})
        if event in ("subprocess.Popen", "os.exec", "os.posix_spawn"):
            # Popen: (executable, args, cwd, env)、os.exec: (path, args, env)、posix_spawn: (path, argv, env)
            first = args[0]
            key = "executable" if event == "subprocess.Popen" else "path"
            argv = argv_list(args[1]) if len(args) > 1 else []
            return self.envelope(
                event, {key: None if first is None else trunc(first), "argv": argv}
            )
        if event == "os.system":
            return self.envelope(event, {"command": trunc(args[0])})
        if event == "socket.connect":
            address = args[1]
            if isinstance(address, tuple):
                shown: Any = [trunc(address[0]), address[1] if len(address) > 1 else None]
            else:
                shown = trunc(address)
            return self.envelope(event, {"address": shown})
        if event == "socket.getaddrinfo":
            host, port = args[0], args[1] if len(args) > 1 else None
            return self.envelope(
                event,
                {
                    "host": "" if host is None else trunc(host),
                    "port": port
                    if isinstance(port, int)
                    else (None if port is None else trunc(port)),
                },
            )
        return None


def install() -> Recorder | None:
    """ログ用の fd を開いてフックを登録する。環境変数が無い・開けないときは何もしない。"""
    log_dir = os.environ.get(LOG_DIR_ENV)
    if not log_dir:
        return None
    try:
        name = f"{os.getpid()}-{time.monotonic_ns()}.jsonl"
        fd = os.open(
            os.path.join(log_dir, name),
            os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_CLOEXEC", 0),
            0o644,
        )
        recorder = Recorder(fd)
        orig = getattr(sys, "orig_argv", sys.argv)
        recorder.write(recorder.envelope("hook.start", {"argv": argv_list(orig)}))
        sys.addaudithook(recorder.handle)
        return recorder
    except Exception:  # noqa: BLE001 - 計測器はホストのプログラムに例外を伝えない
        return None


_RECORDER = install()
