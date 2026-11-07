"""コーディング環境の作業領域 RepoEnv（docs/plan/coding/SPEC.md 6 節、W1-I3）。

作業ツリー・偽 HOME・/tmp の 3 領域を、内容アドレス方式のスナップショット（目録 ＋ blob）で
往復できるようにする。ファイルは 3 つに分類する（`classify_path`）:

- hashed    : 作業ツリーのうち下の 2 つ以外。目録に載り、`state_hash` に入り、`restore` で戻す
- restored  : `.git/**`・`.venv/**`・偽 HOME・/tmp。目録に載り、`restore` で戻すが、`state_hash` には入れない
- 除外      : `__pycache__` など。目録に載らず、`restore` で消す

テストの実行は Executor 経由だけで、ホストでは任意のコードを実行しない。
`repo.py` は `sandbox.py` を import しない（Executor は ExecutorFactory で受け取る）。
"""

from __future__ import annotations

import difflib
import errno
import fnmatch
import hashlib
import io
import json
import os
import re
import shutil
import stat
import uuid
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal, Self

from agenteval.core.coding_types import (
    CODING_DATA_ROOT,
    CONTAINER_REPORTS,
    INIT_STEP,
    REPO_FIXTURE_ROOT,
    SCAN_STEP,
    Area,
    ChangeKind,
    CodingSnapshotRef,
    CodingTask,
    ExecResult,
    Executor,
    ExecutorFactory,
    FileChange,
    FileClass,
    ManifestEntry,
    ModeDir,
    PytestCase,
    PytestOutcome,
    PytestReport,
    RepoDiff,
    SandboxMounts,
    SnapshotManifest,
)
from agenteval.core.normalize import sha256_of
from agenteval.core.schema import Effect, Run

AREAS: tuple[Area, ...] = ("repo", "home", "tmp")

# テスト設定（SPEC 6.6 節）。conftest.py・pytest.ini・tox.ini・setup.cfg・pyproject.toml と tests/** 。
TEST_CONFIG_NAMES: tuple[str, ...] = (
    "conftest.py",
    "pytest.ini",
    "tox.ini",
    "setup.cfg",
    "pyproject.toml",
)
HIDDEN_DIR = "tests_hidden"
SYSTEM_PYTHON = "/usr/local/bin/python"

GIT_FIXED_DATE = "2026-01-01T00:00:00Z"
GIT_AUTHOR_NAME = "agenteval"
GIT_AUTHOR_EMAIL = "agenteval@example.invalid"
PREP_TIMEOUT_S = 120.0

MAX_TEXT_BYTES = 256 * 1024  # これを超えるファイルは差分本文を作らない（binary=True 扱い）
MAX_DIFF_BYTES = 64 * 1024  # 1 ファイルの unified diff の上限
MAX_JUNIT_BYTES = 8 * 1024 * 1024

_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_EPHEMERAL_DIRS = frozenset({"__pycache__", ".pytest_cache"})
_SUMMARY_RE = re.compile(r"(\d+) (passed|failed|errors?|skipped|xfailed|xpassed)")


class PathEscapeError(ValueError):
    """作業ツリーの外を指すパス（絶対パス、..、途中のシンボリックリンク）。"""


# --- 作業領域のレイアウト（SPEC 6.1 節） ----------------------------------------
def _check_run_id(run_id: str) -> None:
    """run_id をパスの一部に使うので、区切りや .. を含むものを拒否する。"""
    if not _RUN_ID_RE.match(run_id) or run_id in (".", ".."):
        raise ValueError(f"run_id がパスに使えない形式: {run_id!r}")


@dataclass(frozen=True)
class WorkLayout:
    """1 run の生成物の置き場。"""

    root: Path  # data/coding/work/<mode>/<run_id>
    mounts: SandboxMounts  # root/{repo,home,tmp,effects,reports}
    snapshot_dir: Path  # data/coding/snapshots/<mode>/<run_id>
    blob_dir: Path  # data/coding/blobs
    eval_root: Path  # data/coding/eval/<mode>/<run_id>

    @classmethod
    def for_run(
        cls, run_id: str, mode_dir: ModeDir, data_root: Path = CODING_DATA_ROOT
    ) -> WorkLayout:
        """run_id と mode から全パスを決める。"""
        _check_run_id(run_id)
        root = data_root / "work" / mode_dir / run_id
        return cls(
            root=root,
            mounts=_mounts_under(root),
            snapshot_dir=data_root / "snapshots" / mode_dir / run_id,
            blob_dir=data_root / "blobs",
            eval_root=data_root / "eval" / mode_dir / run_id,
        )

    def eval_mounts(self, label: str) -> SandboxMounts:
        """隠しテスト・最終評価の一時コピー用の mounts（eval/<mode>/<run_id>/<label>/）。"""
        return _mounts_under(self.eval_root / label)

    def area_root(self, area: Area) -> Path:
        """領域のホスト側ルート。"""
        return {"repo": self.mounts.repo, "home": self.mounts.home, "tmp": self.mounts.tmp}[area]


def _mounts_under(root: Path) -> SandboxMounts:
    return SandboxMounts(
        repo=root / "repo",
        home=root / "home",
        tmp=root / "tmp",
        effects=root / "effects",
        reports=root / "reports",
    )


# --- ファイルの分類（SPEC 6.2 節）。分類は 1 箇所で決める ---------------------------
def classify_path(area: Area, path: str) -> FileClass | None:
    """領域内の POSIX 相対パスを hashed / restored / None（除外）に分類する。"""
    parts = PurePosixPath(path).parts
    if not parts:
        return None
    if any(p in _EPHEMERAL_DIRS for p in parts[:-1]) or parts[-1].endswith(".pyc"):
        return None
    if area == "tmp" and len(parts) >= 2 and fnmatch.fnmatchcase(parts[0], "pytest-of-*"):
        return None
    if area == "repo":
        return "restored" if parts[0] in (".git", ".venv") else "hashed"
    return "restored"


def is_test_config(path: str) -> bool:
    """作業ツリー相対パスが「テスト設定」（SPEC 6.6 節）か。"""
    parts = PurePosixPath(path).parts
    if not parts:
        return False
    return parts[-1] in TEST_CONFIG_NAMES or (len(parts) >= 2 and parts[0] == "tests")


# --- 低レベルのファイル操作（ホストを守る。シンボリックリンクはたどらない） ---------
@dataclass(frozen=True)
class _Leaf:
    rel: str
    abs: Path
    st: os.stat_result


def _walk(root: Path) -> tuple[list[_Leaf], list[str]]:
    """root 以下のディレクトリでないもの（ファイル・リンク・特殊ファイル）とディレクトリを集める。

    リンクはたどらない（リンク先がディレクトリでも葉として扱う）。除外分類も含めて全部返す。
    """
    leaves: list[_Leaf] = []
    dirs: list[str] = []
    stack: list[tuple[Path, str]] = [(root, "")]
    while stack:
        directory, prefix = stack.pop()
        try:
            iterator = os.scandir(directory)
        except FileNotFoundError:
            continue
        with iterator as entries:
            for entry in entries:
                rel = f"{prefix}{entry.name}"
                if entry.is_dir(follow_symlinks=False):
                    dirs.append(rel)
                    stack.append((Path(entry.path), f"{rel}/"))
                else:
                    try:
                        st = entry.stat(follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    leaves.append(_Leaf(rel, Path(entry.path), st))
    leaves.sort(key=lambda leaf: leaf.rel)
    return leaves, dirs


def _stat_and_read(path: Path) -> tuple[os.stat_result, bytes] | None:
    """lstat してから中身を読む。リンクならリンク先の文字列、通常ファイルならバイト列。

    読むときは O_NOFOLLOW で開くので、lstat の後にリンクへ差し替えられてもリンク先を読まない。
    通常ファイルでも特殊ファイルでもなければ None（呼び出し側が飛ばす）。
    """
    for _ in range(3):
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            return None
        if stat.S_ISLNK(st.st_mode):
            try:
                return st, os.fsencode(os.readlink(path))
            except (FileNotFoundError, OSError):
                continue
        if not stat.S_ISREG(st.st_mode):
            return None
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        try:
            fd = os.open(path, flags)
        except PermissionError:
            # エージェントが chmod 000 したファイルでも目録に載せる（一時的に読み取りを許す）
            os.chmod(path, (st.st_mode & 0o777) | 0o600)
            try:
                fd = os.open(path, flags)
            finally:
                os.chmod(path, st.st_mode & 0o777)
        except FileNotFoundError:
            return None
        except OSError as exc:
            if exc.errno == errno.ELOOP:  # lstat の後にリンクへ差し替えられた。やり直す
                continue
            raise
        with os.fdopen(fd, "rb") as f:
            return st, f.read()
    return None


def _read_regular(path: Path) -> bytes | None:
    """通常ファイルだけを読む（リンクは None）。レポート XML など、読む先が信用できないときに使う。"""
    got = _stat_and_read(path)
    if got is None or stat.S_ISLNK(got[0].st_mode):
        return None
    return got[1]


def _rmtree(path: Path) -> None:
    """権限で消せないものがあっても消す。リンクはたどらない。無ければ何もしない。"""

    def onexc(func: Callable[[str], object], p: str, exc: BaseException) -> None:
        if isinstance(exc, FileNotFoundError):
            return
        os.chmod(os.path.dirname(p), 0o700)
        if not os.path.islink(p):
            os.chmod(p, 0o700)
        func(p)

    if os.path.lexists(path):
        if os.path.islink(path) or not os.path.isdir(path):
            os.unlink(path)
        else:
            shutil.rmtree(path, onexc=onexc)


def _ensure_parent_dirs(root: Path, rel: str) -> None:
    """rel の親ディレクトリを root から順に作る。途中がリンクやファイルなら消して作り直す。"""
    cur = root
    for part in PurePosixPath(rel).parent.parts:
        cur = cur / part
        try:
            st = os.lstat(cur)
        except FileNotFoundError:
            os.mkdir(cur)
            continue
        if stat.S_ISDIR(st.st_mode):
            continue
        os.unlink(cur)
        os.mkdir(cur)


def _put(path: Path, data: bytes, mode: int, *, symlink: bool) -> None:
    """一時名に書いて os.replace する（既存のリンクをたどらず、リンク自体を置き換える）。"""
    tmp = path.with_name(f".agenteval-tmp-{uuid.uuid4().hex}")
    if symlink:
        os.symlink(os.fsdecode(data), tmp)
    else:
        tmp.write_bytes(data)
        os.chmod(tmp, mode)
    if os.path.isdir(path) and not os.path.islink(path):
        _rmtree(path)
    os.replace(tmp, path)


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """blob・目録用。一時ファイルに書いて os.replace（並列の runner が同じ blob を書いても壊れない）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _safe_join(root: Path, rel: str) -> Path:
    """root からの相対パス rel をホストの絶対パスにする（SPEC 6.7 節）。

    絶対パス・..・NUL、root から rel までの途中にあるシンボリックリンク（rel 自身がリンクの場合も）は
    PathEscapeError。存在しない部分は検査できないのでそのまま通す（作成先として使える）。
    """
    pure = PurePosixPath(rel)
    if "\x00" in rel or rel.startswith("/") or pure.is_absolute() or ".." in pure.parts:
        raise PathEscapeError(f"作業領域の外を指すパス: {rel!r}")
    cur = root
    for part in pure.parts:
        cur = cur / part
        try:
            st = os.lstat(cur)
        except (FileNotFoundError, NotADirectoryError):
            break
        if stat.S_ISLNK(st.st_mode):
            raise PathEscapeError(f"シンボリックリンクを含むパス: {rel!r}")
    return root.joinpath(*pure.parts)


def _split_target(target: str) -> tuple[Area, str]:
    """対象文字列（repo:x、home:x、tmp:x の形）を (領域, パス) に分ける。"""
    area, sep, path = target.partition(":")
    if not sep or area not in AREAS:
        raise ValueError(f"領域の接頭辞（repo: / home: / tmp:）が要る: {target!r}")
    return area, path


# --- 目録の作成（SPEC 6.3・6.4 節） ----------------------------------------------
def _digest_of(data: bytes, blob: str, canaries: Mapping[str, str]) -> str:
    """カナリアの値を {{canary:<id>}} に置き換えてから sha256 する。含まなければ blob と同じ。"""
    normalized = data
    for canary_id, value in sorted(canaries.items()):
        if not value:
            continue
        encoded = value.encode("utf-8")
        if encoded in normalized:
            normalized = normalized.replace(encoded, f"{{{{canary:{canary_id}}}}}".encode())
    if normalized is data:
        return blob
    return hashlib.sha256(normalized).hexdigest()


def _scan_area(
    root: Path,
    area: Area,
    canaries: Mapping[str, str],
    blob_sink: Callable[[str, bytes], None] | None,
    *,
    only_hashed: bool = False,
) -> dict[str, ManifestEntry]:
    """領域の目録。除外分類と通常ファイル・リンク以外は載せない。"""
    if not root.is_dir():
        raise FileNotFoundError(f"領域のルートが無い: {root}")
    leaves, _ = _walk(root)
    entries: dict[str, ManifestEntry] = {}
    for leaf in leaves:
        cls = classify_path(area, leaf.rel)
        if cls is None or (only_hashed and cls != "hashed"):
            continue
        got = _stat_and_read(leaf.abs)
        if got is None:
            continue
        st, data = got
        is_link = stat.S_ISLNK(st.st_mode)
        blob = hashlib.sha256(data).hexdigest()
        digest = blob if is_link else _digest_of(data, blob, canaries)
        if blob_sink is not None:
            blob_sink(blob, data)
        entries[leaf.rel] = ManifestEntry(
            blob=blob,
            digest=digest,
            size=len(data),
            mode=st.st_mode & 0o777,
            cls=cls,
            symlink=is_link,
        )
    return entries


def compute_state_hash(repo_entries: Mapping[str, ManifestEntry]) -> str:
    """state_hash = hashed なファイルの [path, digest] を path 順に並べた正規 JSON の sha256。"""
    return sha256_of(
        [[path, e.digest] for path, e in sorted(repo_entries.items()) if e.cls == "hashed"]
    )


def _manifest_from(
    run_id: str, step: int, areas: dict[Area, dict[str, ManifestEntry]]
) -> SnapshotManifest:
    return SnapshotManifest(
        run_id=run_id,
        step=step,
        state_hash=compute_state_hash(areas["repo"]),
        areas=areas,
    )


def _blob_path(blob_dir: Path, blob: str) -> Path:
    return blob_dir / blob[:2] / blob


# --- フィクスチャの展開（SPEC 7.1 節） --------------------------------------------
def _initial_file_map(task: CodingTask, fixture_root: Path) -> dict[str, Path]:
    """初期状態の作業ツリー = repo/ ⊕ overlay − delete_paths。相対パス -> フィクスチャ内のファイル。

    .git・.venv・.env（`RepoEnv.create` が作るもの）と除外分類は含めない。
    """
    base = fixture_root / task.repo
    repo_dir = base / "repo"
    if not repo_dir.is_dir():
        raise FileNotFoundError(f"フィクスチャの repo/ が無い: {repo_dir}")
    files: dict[str, Path] = {}
    for source in (repo_dir, base / "tasks" / task.id / "overlay"):
        if not source.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(source):
            for name in [*dirnames, *filenames]:
                if os.path.islink(os.path.join(dirpath, name)):
                    raise ValueError(
                        f"フィクスチャにシンボリックリンクは置けない: {dirpath}/{name}"
                    )
            here = Path(dirpath).relative_to(source).as_posix()
            if here == "." and ".git" in dirnames:
                dirnames.remove(".git")
            if here == "." and ".venv" in dirnames:
                dirnames.remove(".venv")
            for name in filenames:
                rel = name if here == "." else f"{here}/{name}"
                if rel == ".env" or classify_path("repo", rel) is None:
                    continue
                files[rel] = Path(dirpath) / name
    for deleted in task.delete_paths:
        pure = PurePosixPath(deleted)
        if deleted.startswith("/") or ".." in pure.parts:
            raise PathEscapeError(f"delete_paths が作業ツリーの外を指す: {deleted!r}")
        key = pure.as_posix()
        files.pop(key, None)
        for rel in [r for r in files if r.startswith(f"{key}/")]:
            del files[rel]
    return files


# --- JUnit XML → PytestReport（SPEC 6.6 節） --------------------------------------
def _junit_nodeid(testcase: ET.Element) -> str:
    """JUnit の classname/name から node id を復元する（近似）。

    xunit2 には file 属性が無いので、classname を「小文字始まりの要素 = モジュールのパス、
    大文字始まり以降 = クラス」とみなして組み立てる。file 属性があればそれを使う。
    """
    name = testcase.get("name", "")
    classname = testcase.get("classname", "")
    file_attr = testcase.get("file")
    parts = classname.split(".") if classname else []
    idx = next((i for i, p in enumerate(parts) if p[:1].isupper()), len(parts))
    class_parts = parts[idx:]
    if file_attr:
        return "::".join([file_attr, *class_parts, name])
    if not parts:
        return name
    return "::".join(["/".join(parts[:idx]) + ".py", *class_parts, name])


def _parse_junit(raw: bytes) -> list[PytestCase] | None:
    """JUnit XML のテストケース。解釈できなければ None。

    XML は run 中に書き込まれうる（エージェントのコードが同じ場所に書ける）ので、サイズ上限を設け、
    DOCTYPE と ENTITY を含むものは読まない（実体展開攻撃の回避）。
    """
    if len(raw) > MAX_JUNIT_BYTES or b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:
        return None
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return None
    cases: list[PytestCase] = []
    for tc in root.iter("testcase"):
        outcome: PytestOutcome = "passed"
        for child in tc:
            if child.tag == "failure":
                outcome = "failed"
            elif child.tag == "error":
                outcome = "error"
            elif child.tag == "skipped":
                kind = f"{child.get('type', '')} {child.get('message', '')}".lower()
                outcome = "xfailed" if "xfail" in kind else "skipped"
            else:
                continue
            break
        try:
            duration_ms = int(float(tc.get("time", "0")) * 1000)
        except ValueError:
            duration_ms = 0
        cases.append(PytestCase(nodeid=_junit_nodeid(tc), outcome=outcome, duration_ms=duration_ms))
    return cases


def _counts_from_output(output: str) -> dict[str, int]:
    """XML が無いときの代替。pytest の最終行（"2 passed, 1 failed in 0.1s"）から件数を読む。"""
    counts = {"passed": 0, "failed": 0, "error": 0, "skipped": 0}
    for line in reversed(output.strip().splitlines()):
        if " in " not in line or not _SUMMARY_RE.search(line):
            continue
        for number, word in _SUMMARY_RE.findall(line):
            key = "error" if word.startswith("error") else word
            if key in counts:
                counts[key] += int(number)
        break
    return counts


def _build_report(
    suite: Literal["visible", "hidden"], result: ExecResult, xml_path: Path
) -> PytestReport:
    """exec の結果と JUnit XML から PytestReport を作る。passed の定義は SPEC 6.6 節。"""
    raw = _read_regular(xml_path)
    cases = _parse_junit(raw) if raw is not None else None
    output = result.stdout + (f"\n{result.stderr}" if result.stderr else "")
    if cases is not None:
        n_passed = sum(c.outcome == "passed" for c in cases)
        n_failed = sum(c.outcome == "failed" for c in cases)
        n_error = sum(c.outcome == "error" for c in cases)
        n_skipped = sum(c.outcome == "skipped" for c in cases)
    else:
        cases = []
        counts = _counts_from_output(result.stdout)
        n_passed, n_failed = counts["passed"], counts["failed"]
        n_error, n_skipped = counts["error"], counts["skipped"]
    passed = (
        result.exit_code == 0
        and not result.timed_out
        and n_failed == 0
        and n_error == 0
        and n_passed >= 1
    )
    return PytestReport(
        suite=suite,
        argv=list(result.argv),
        exit_code=result.exit_code,
        passed=passed,
        n_passed=n_passed,
        n_failed=n_failed,
        n_error=n_error,
        n_skipped=n_skipped,
        cases=cases,
        output=output,
        duration_ms=result.duration_ms,
        timed_out=result.timed_out,
        effects=list(result.effects),
    )


def _check_selector(selector: Sequence[str]) -> None:
    """オプション注入・作業ツリー外を指す selector を拒否する（SPEC 6.6 節）。"""
    for item in selector:
        path_part = item.split("::", 1)[0]
        if (
            not item
            or item.startswith("-")
            or path_part.startswith("/")
            or ".." in PurePosixPath(path_part).parts
            or "\x00" in item
        ):
            raise ValueError(f"selector に使えない値: {item!r}")


# --- 差分（SPEC 6.5 節 diff） ------------------------------------------------------
def _as_text(data: bytes) -> str | None:
    """差分本文を作れるテキストなら文字列、そうでなければ None（バイナリ扱い）。"""
    if len(data) > MAX_TEXT_BYTES or b"\x00" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _unified(path: str, before: str, after: str) -> tuple[str, int, int, bool]:
    """(unified diff, 追加行数, 削除行数, 切り詰めたか)。a/<path>・b/<path>、文脈 3 行。"""
    lines = list(
        difflib.unified_diff(
            io.StringIO(before).readlines(),
            io.StringIO(after).readlines(),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            n=3,
        )
    )
    added = sum(1 for ln in lines[2:] if ln.startswith("+"))
    removed = sum(1 for ln in lines[2:] if ln.startswith("-"))
    fixed = [ln if ln.endswith("\n") else f"{ln}\n\\ No newline at end of file\n" for ln in lines]
    text = "".join(fixed)
    encoded = text.encode("utf-8")
    if len(encoded) > MAX_DIFF_BYTES:
        return encoded[:MAX_DIFF_BYTES].decode("utf-8", errors="ignore"), added, removed, True
    return text, added, removed, False


# --- RepoEnv ------------------------------------------------------------------------
class RepoEnv:
    """コーディング環境の作業領域。`create` で作り、`close` で片付ける。"""

    task: CodingTask
    run_id: str
    mode_dir: ModeDir
    layout: WorkLayout
    executor: Executor

    def __init__(
        self,
        task: CodingTask,
        run_id: str,
        mode_dir: ModeDir,
        layout: WorkLayout,
        executor: Executor,
        *,
        executor_factory: ExecutorFactory,
        canaries: Mapping[str, str],
        fixture_root: Path,
    ) -> None:
        """直接は呼ばず `create` を使う。"""
        self.task = task
        self.run_id = run_id
        self.mode_dir = mode_dir
        self.layout = layout
        self.executor = executor
        self._executor_factory = executor_factory
        self._canaries: dict[str, str] = dict(canaries)
        self._fixture_root = fixture_root
        self._report_no = 0

    # --- 作成と片付け -----------------------------------------------------------
    @classmethod
    def create(
        cls,
        task: CodingTask,
        run_id: str,
        *,
        executor_factory: ExecutorFactory,
        mode_dir: ModeDir,
        extra_files: Mapping[str, str] | None = None,
        canaries: Mapping[str, str] | None = None,
        data_root: Path = CODING_DATA_ROOT,
        fixture_root: Path = REPO_FIXTURE_ROOT,
        init_git: bool = True,
    ) -> RepoEnv:
        """作業領域を作り、フィクスチャを展開し、executor を start し、初期状態の目録 init.json を保存する。

        docker のときは作業ツリーで決定的な git init と初回コミット、続けて .venv の作成を行う
        （fake では作らない）。準備の効果は Step.effects に入れない。作業領域が既にあれば
        FileExistsError（上書きしない）。
        """
        layout = WorkLayout.for_run(run_id, mode_dir, data_root)
        if layout.root.exists():
            raise FileExistsError(f"作業領域が既にある（上書きしない）: {layout.root}")
        mounts = layout.mounts
        executor: Executor | None = None
        try:
            for directory in (
                mounts.repo,
                mounts.home,
                mounts.tmp,
                mounts.effects,
                mounts.reports,
            ):
                directory.mkdir(parents=True)
            for rel, source in _initial_file_map(task, fixture_root).items():
                dest = mounts.repo.joinpath(*PurePosixPath(rel).parts)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, dest)
            for target, content in (extra_files or {}).items():
                area, rel = _split_target(target)
                dest = _safe_join(layout.area_root(area), rel)
                _ensure_parent_dirs(layout.area_root(area), rel)
                dest.write_text(content, encoding="utf-8")
            executor = executor_factory(mounts)
            if executor.mounts != mounts:
                raise ValueError("executor の mounts が作業領域の mounts と違う")
            executor.start()
            env = cls(
                task,
                run_id,
                mode_dir,
                layout,
                executor,
                executor_factory=executor_factory,
                canaries=canaries or {},
                fixture_root=fixture_root,
            )
            if executor.kind == "docker" and init_git:
                env._prepare_git_and_venv()
            env.snapshot(INIT_STEP)
            return env
        except BaseException:
            if executor is not None:
                executor.stop()
            _rmtree(layout.root)
            raise

    def _prepare(self, argv: Sequence[str], env: Mapping[str, str] | None = None) -> None:
        """準備のコマンドを実行する。失敗したら RuntimeError（効果は捨てる）。"""
        result = self.executor.exec(argv, timeout_s=PREP_TIMEOUT_S, env=env)
        if result.exit_code != 0:
            raise RuntimeError(
                f"作業領域の準備に失敗: {' '.join(argv)} (exit={result.exit_code}): "
                f"{result.stderr.strip()[:300]}"
            )

    def _prepare_git_and_venv(self) -> None:
        """決定的な git init と初回コミット、続けて .venv の作成（SPEC 6.5 節）。"""
        self._prepare(["git", "init", "-q", "--initial-branch=main"])
        self._prepare(["git", "config", "user.name", GIT_AUTHOR_NAME])
        self._prepare(["git", "config", "user.email", GIT_AUTHOR_EMAIL])
        self._prepare(["git", "add", "-A"])
        self._prepare(
            ["git", "commit", "-q", "--allow-empty", "--no-gpg-sign", "-m", "initial"],
            env={"GIT_AUTHOR_DATE": GIT_FIXED_DATE, "GIT_COMMITTER_DATE": GIT_FIXED_DATE},
        )
        self._prepare(["python", "-m", "venv", "--system-site-packages", "--without-pip", ".venv"])

    def close(self, *, remove_workdir: bool = True) -> None:
        """executor を stop し、remove_workdir なら work/<mode>/<run_id>/ を消す。冪等。"""
        self.executor.stop()
        if remove_workdir:
            _rmtree(self.layout.root)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- 状態 --------------------------------------------------------------------
    def _scan(self, step: int, blob_sink: Callable[[str, bytes], None] | None) -> SnapshotManifest:
        areas: dict[Area, dict[str, ManifestEntry]] = {
            area: _scan_area(self.layout.area_root(area), area, self._canaries, blob_sink)
            for area in AREAS
        }
        return _manifest_from(self.run_id, step, areas)

    def scan(self) -> SnapshotManifest:
        """現在の状態の目録（step=-2）。blob も目録も書かない。"""
        return self._scan(SCAN_STEP, None)

    def _write_blob(self, blob: str, data: bytes) -> None:
        path = _blob_path(self.layout.blob_dir, blob)
        if not path.exists():
            _atomic_write_bytes(path, data)

    def snapshot(self, step: int) -> CodingSnapshotRef:
        """blob と目録を書き、参照を返す。step=-1 は init.json、それ以外は step_<i>.json。"""
        if step < INIT_STEP:
            raise ValueError(f"step は -1（init）以上: {step}")
        manifest = self._scan(step, self._write_blob)
        path = self.layout.snapshot_dir / (
            "init.json" if step == INIT_STEP else f"step_{step}.json"
        )
        payload = json.dumps(manifest.model_dump(mode="json"), sort_keys=True, indent=1)
        _atomic_write_bytes(path, payload.encode("utf-8"))
        return CodingSnapshotRef(run_id=self.run_id, step=step, path=path)

    def load_manifest(self, ref: CodingSnapshotRef) -> SnapshotManifest:
        """保存した目録を読む。"""
        return SnapshotManifest.model_validate_json(ref.path.read_text(encoding="utf-8"))

    def _init_ref(self) -> CodingSnapshotRef:
        return CodingSnapshotRef(
            run_id=self.run_id, step=INIT_STEP, path=self.layout.snapshot_dir / "init.json"
        )

    def _read_blob(self, blob: str) -> bytes:
        return _blob_path(self.layout.blob_dir, blob).read_bytes()

    def restore(self, ref: CodingSnapshotRef) -> None:
        """3 領域を目録どおりに戻す。目録に無いファイル（除外分類を含む）を消し、blob から書き戻し、mode を戻す。

        復元後の state_hash() は目録の state_hash と一致する。コンテナは止めない。
        空のディレクトリは目録に載らないので追跡しない（.git と .venv の中は触らない）。
        """
        manifest = self.load_manifest(ref)
        for area in AREAS:
            self._restore_area(area, manifest.areas.get(area, {}))

    def _restore_area(self, area: Area, entries: Mapping[str, ManifestEntry]) -> None:
        root = self.layout.area_root(area)
        leaves, dirs = _walk(root)
        for stale in leaves:
            if stale.rel not in entries:
                os.unlink(stale.abs)
        current = {lf.rel: lf for lf in leaves if lf.rel in entries}
        for rel, entry in sorted(entries.items()):
            _ensure_parent_dirs(root, rel)
            dest = root.joinpath(*PurePosixPath(rel).parts)
            leaf = current.get(rel)
            if leaf is not None and self._same_as_entry(leaf, entry):
                if not entry.symlink and (leaf.st.st_mode & 0o777) != entry.mode:
                    os.chmod(dest, entry.mode)
                continue
            _put(dest, self._read_blob(entry.blob), entry.mode, symlink=entry.symlink)
        needed = {parent.as_posix() for rel in entries for parent in PurePosixPath(rel).parents}
        for rel in sorted(dirs, key=lambda d: d.count("/"), reverse=True):
            if rel in needed or (area == "repo" and rel.split("/", 1)[0] in (".git", ".venv")):
                continue
            try:
                os.rmdir(root / rel)
            except OSError:
                pass  # 空でなければ残す

    def _same_as_entry(self, leaf: _Leaf, entry: ManifestEntry) -> bool:
        """現在のファイルが目録の内容（種類・中身）と同じか。mode は別に直す。"""
        is_link = stat.S_ISLNK(leaf.st.st_mode)
        if is_link != entry.symlink or not (is_link or stat.S_ISREG(leaf.st.st_mode)):
            return False
        if not is_link and leaf.st.st_size != entry.size:
            return False
        got = _stat_and_read(leaf.abs)
        return got is not None and hashlib.sha256(got[1]).hexdigest() == entry.blob

    def state_hash(self) -> str:
        """現在の作業ツリーの state_hash（hashed なファイルだけを走査する）。"""
        entries = _scan_area(
            self.layout.mounts.repo, "repo", self._canaries, None, only_hashed=True
        )
        return compute_state_hash(entries)

    def diff(
        self,
        base: SnapshotManifest | CodingSnapshotRef,
        target: SnapshotManifest | CodingSnapshotRef | None = None,
        *,
        areas: Sequence[Area] = ("repo",),
        include_git: bool = False,
    ) -> RepoDiff:
        """ファイル単位の差分。

        テキスト（UTF-8 で読め 256 KiB 以下）は unified diff（64 KiB まで、超えたら truncated）、
        バイナリは binary=True で本文なし。内容が同じで mode だけ違うファイルは modified（本文なし）。
        blob を書いていない目録（scan()）どうしの比較では本文を作れず、本文は空になる。
        """
        base_manifest, base_live = self._resolve(base)
        target_manifest, target_live = self._resolve(target)
        files: list[FileChange] = []
        for area in areas:
            before = base_manifest.areas.get(area, {})
            after = target_manifest.areas.get(area, {})
            for path in sorted(before.keys() | after.keys()):
                if not include_git and area == "repo" and path.split("/", 1)[0] == ".git":
                    continue
                old, new = before.get(path), after.get(path)
                if old is not None and new is not None and self._same_entry(old, new):
                    continue
                change: ChangeKind = (
                    "added" if old is None else "deleted" if new is None else "modified"
                )
                files.append(
                    self._file_change(area, path, change, (old, base_live), (new, target_live))
                )
        return RepoDiff(
            base=self._label(base_manifest, base_live),
            target=self._label(target_manifest, target_live),
            files=files,
        )

    @staticmethod
    def _same_entry(a: ManifestEntry, b: ManifestEntry) -> bool:
        return a.blob == b.blob and a.symlink == b.symlink and a.mode == b.mode

    @staticmethod
    def _label(manifest: SnapshotManifest, live: bool) -> str:
        if live:
            return "current"
        return {INIT_STEP: "init", SCAN_STEP: "scan"}.get(manifest.step, f"step_{manifest.step}")

    def _resolve(
        self, source: SnapshotManifest | CodingSnapshotRef | None
    ) -> tuple[SnapshotManifest, bool]:
        """目録（と、それが現在の作業ツリーの走査かどうか）にする。"""
        if source is None:
            return self.scan(), True
        if isinstance(source, CodingSnapshotRef):
            return self.load_manifest(source), False
        return source, False

    def _content(self, area: Area, path: str, entry: ManifestEntry, live: bool) -> bytes | None:
        """差分の本文の元になるバイト列。取れなければ None。"""
        if live:
            got = _stat_and_read(self.layout.area_root(area).joinpath(*PurePosixPath(path).parts))
            return None if got is None else got[1]
        blob = _blob_path(self.layout.blob_dir, entry.blob)
        return blob.read_bytes() if blob.exists() else None

    def _file_change(
        self,
        area: Area,
        path: str,
        change: ChangeKind,
        old: tuple[ManifestEntry | None, bool],
        new: tuple[ManifestEntry | None, bool],
    ) -> FileChange:
        before = b"" if old[0] is None else self._content(area, path, old[0], old[1])
        after = b"" if new[0] is None else self._content(area, path, new[0], new[1])
        if before is None or after is None:
            return FileChange(area=area, path=path, change=change)
        before_text, after_text = _as_text(before), _as_text(after)
        if before_text is None or after_text is None:
            return FileChange(area=area, path=path, change=change, binary=True)
        if before == after:
            return FileChange(area=area, path=path, change=change)
        unified, added, removed, truncated = _unified(path, before_text, after_text)
        return FileChange(
            area=area,
            path=path,
            change=change,
            unified=unified,
            truncated=truncated,
            added_lines=added,
            removed_lines=removed,
        )

    # --- ファイル（ツール実装が使う。ホスト側で直接読み書きする） -------------------
    def safe_path(self, rel: str, *, area: Area = "repo") -> Path:
        """領域のルートからの相対パスをホストの絶対パスに解決する。

        絶対パス、..、途中（と末尾）にシンボリックリンクがあれば PathEscapeError。
        """
        return _safe_join(self.layout.area_root(area), rel)

    def read_area_file(self, target: str) -> str | None:
        """ "repo:x" / "home:x" / "tmp:x" の内容（UTF-8、errors="replace"）。無ければ None。"""
        area, rel = _split_target(target)
        path = self.safe_path(rel, area=area)
        data = _read_regular(path)
        return None if data is None else data.decode("utf-8", errors="replace")

    def list_repo_files(self) -> list[str]:
        """作業ツリーのファイル（.git/** と除外分類を除く）を辞書順で。リンクもパスとして載せる。"""
        leaves, _ = _walk(self.layout.mounts.repo)
        return [
            leaf.rel
            for leaf in leaves
            if (stat.S_ISREG(leaf.st.st_mode) or stat.S_ISLNK(leaf.st.st_mode))
            and leaf.rel.split("/", 1)[0] != ".git"
            and classify_path("repo", leaf.rel) is not None
        ]

    # --- テスト --------------------------------------------------------------------
    def _next_report_no(self) -> int:
        n = self._report_no
        self._report_no += 1
        return n

    def run_visible_tests(
        self,
        selector: Sequence[str] | None = None,
        *,
        timeout_s: float,
        verbosity: Literal["quiet", "verbose"] = "quiet",
        call_id: str | None = None,
        original_tests: bool = False,
        ref: CodingSnapshotRef | None = None,
    ) -> PytestReport:
        """可視テストを走らせる。

        original_tests=False: 現在の作業ツリーで、エージェントが変えたテストもそのまま走らせる。
        original_tests=True: 最終評価。対象の状態からテスト設定を除き、初期状態のテスト設定を書き戻した
        一時コピーを、隠しテストと同じ隔離した起動（python -I -B）で走らせる。ref は True のときだけ有効。
        """
        chosen = list(self.task.visible_tests if selector is None else selector)
        _check_selector(chosen)
        n = self._next_report_no()
        if original_tests:
            return self._evaluate(
                suite="visible",
                label=f"final-{n}",
                report_no=n,
                ref=ref,
                hidden=False,
                targets=chosen,
                timeout_s=timeout_s,
                call_id=call_id,
            )
        flags = ["-q", "--tb=short"] if verbosity == "quiet" else ["-vv", "--tb=long", "-rA"]
        argv = [
            "python",
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            f"--junitxml={CONTAINER_REPORTS}/{n}.xml",
            *flags,
            *chosen,
        ]
        result = self.executor.exec(argv, timeout_s=timeout_s, call_id=call_id)
        return _build_report("visible", result, self.layout.mounts.reports / f"{n}.xml")

    def _test_config_blobs(self, entries: Mapping[str, ManifestEntry]) -> dict[str, str]:
        return {
            path: e.blob
            for path, e in entries.items()
            if e.cls == "hashed" and is_test_config(path)
        }

    def test_files_modified(self, ref: CodingSnapshotRef | None = None) -> list[str]:
        """初期状態から内容が変わった（消えた・増えた）テスト設定ファイルのパス。"""
        initial = self._test_config_blobs(self.load_manifest(self._init_ref()).areas["repo"])
        if ref is None:
            current_entries = _scan_area(
                self.layout.mounts.repo, "repo", self._canaries, None, only_hashed=True
            )
        else:
            current_entries = self.load_manifest(ref).areas["repo"]
        current = self._test_config_blobs(current_entries)
        return sorted(
            p for p in initial.keys() | current.keys() if initial.get(p) != current.get(p)
        )

    def run_hidden_tests(
        self,
        ref: CodingSnapshotRef | None = None,
        *,
        timeout_s: float = 120.0,
    ) -> PytestReport:
        """隠しテストを、対象の状態のテスト設定・.venv・.git を除いた一時コピーで走らせる。

        効果は PytestReport.effects に残し、Step.effects には入れない（呼び出し側の責務）。
        """
        n = self._next_report_no()
        return self._evaluate(
            suite="hidden",
            label=f"hidden-{n}",
            report_no=n,
            ref=ref,
            hidden=True,
            targets=[HIDDEN_DIR],
            timeout_s=timeout_s,
            call_id=None,
        )

    def _evaluate(
        self,
        *,
        suite: Literal["visible", "hidden"],
        label: str,
        report_no: int,
        ref: CodingSnapshotRef | None,
        hidden: bool,
        targets: Sequence[str],
        timeout_s: float,
        call_id: str | None,
    ) -> PytestReport:
        """eval/<mode>/<run_id>/<label>/ に一時コピーを作り、新しい Executor で隔離して走らせる。"""
        eval_dir = self.layout.eval_root / label
        mounts = self.layout.eval_mounts(label)
        _rmtree(eval_dir)
        executor: Executor | None = None
        try:
            for directory in (mounts.repo, mounts.home, mounts.tmp, mounts.effects, mounts.reports):
                directory.mkdir(parents=True)
            config = self._write_eval_tree(mounts.repo, ref, hidden=hidden)
            executor = self._executor_factory(mounts)
            executor.start()
            argv = [
                SYSTEM_PYTHON,
                "-I",
                "-B",
                "-m",
                "pytest",
                *(["-c", config] if config else []),
                "--rootdir",
                ".",
                "-p",
                "no:cacheprovider",
                "-q",
                f"--junitxml={CONTAINER_REPORTS}/{report_no}.xml",
                *targets,
            ]
            result = executor.exec(argv, timeout_s=timeout_s, call_id=call_id)
            return _build_report(suite, result, mounts.reports / f"{report_no}.xml")
        finally:
            try:
                if executor is not None:
                    executor.stop()
            finally:
                _rmtree(eval_dir)

    def _write_eval_tree(
        self, dest: Path, ref: CodingSnapshotRef | None, *, hidden: bool
    ) -> str | None:
        """一時コピーの作業ツリーを作り、pytest の -c に渡す設定ファイルのパス（無ければ None）を返す。

        対象の状態の hashed ファイルからテスト設定（と既存の tests_hidden/）を除いて書き出す。
        hidden なら fixture の hidden/ から隠しテストを置き、そうでなければ初期状態のテスト設定を書き戻す。
        """

        def skip(rel: str) -> bool:
            return is_test_config(rel) or rel.split("/", 1)[0] == HIDDEN_DIR

        def read(rel: str, entry: ManifestEntry) -> bytes:
            return self._current_bytes(rel) if ref is None else self._read_blob(entry.blob)

        if ref is None:
            entries = _scan_area(
                self.layout.mounts.repo, "repo", self._canaries, None, only_hashed=True
            )
        else:
            entries = self.load_manifest(ref).areas["repo"]
        for rel, entry in sorted(entries.items()):
            if entry.cls != "hashed" or skip(rel):
                continue
            self._write_entry(dest, rel, entry, read(rel, entry))
        if hidden:
            return self._place_hidden_tests(dest)
        initial = self.load_manifest(self._init_ref()).areas["repo"]
        restored = {
            rel: e for rel, e in initial.items() if e.cls == "hashed" and is_test_config(rel)
        }
        for rel, entry in sorted(restored.items()):
            self._write_entry(dest, rel, entry, self._read_blob(entry.blob))
        for name in ("pyproject.toml", "pytest.ini"):
            if name in restored:
                return name
        return None

    def _current_bytes(self, rel: str) -> bytes:
        got = _stat_and_read(self.layout.mounts.repo.joinpath(*PurePosixPath(rel).parts))
        if got is None:
            raise FileNotFoundError(f"評価用コピーの途中でファイルが消えた: {rel}")
        return got[1]

    @staticmethod
    def _write_entry(dest: Path, rel: str, entry: ManifestEntry, data: bytes) -> None:
        _ensure_parent_dirs(dest, rel)
        _put(dest.joinpath(*PurePosixPath(rel).parts), data, entry.mode, symlink=entry.symlink)

    def _place_hidden_tests(self, dest: Path) -> str:
        """fixture の hidden/ から task.hidden_tests を tests_hidden/ に置く。-c に渡すパスを返す。"""
        names = [PurePosixPath(p).name for p in self.task.hidden_tests]
        if len(set(names)) != len(names):
            raise ValueError(f"隠しテストのファイル名が重複している: {self.task.hidden_tests}")
        hidden_dir = dest / HIDDEN_DIR
        _rmtree(hidden_dir)
        hidden_dir.mkdir()
        source_root = self._fixture_root / self.task.repo / "hidden"
        for relpath, name in zip(self.task.hidden_tests, names, strict=True):
            pure = PurePosixPath(relpath)
            if pure.is_absolute() or ".." in pure.parts:
                raise PathEscapeError(f"hidden_tests が hidden/ の外を指す: {relpath!r}")
            shutil.copyfile(source_root.joinpath(*pure.parts), hidden_dir / name)
        (hidden_dir / "pytest.ini").write_text(
            "[pytest]\naddopts =\npythonpath = src\n", encoding="utf-8"
        )
        return f"{HIDDEN_DIR}/pytest.ini"

    # --- 静的 ------------------------------------------------------------------------
    @staticmethod
    def touched_files(run: Run) -> list[str]:
        """fs_diff の fs_write のうち repo: 配下で .git/ 以外のものの相対パスの集合を辞書順で。

        途中で書いて元に戻したファイルも含む。
        """
        paths: set[str] = set()
        for step in run.steps:
            # Step.effects は W1-F2 で core/schema.py に入る。入るまでは型に無いので getattr で読む。
            effects: Sequence[Effect] = getattr(step, "effects", ())
            for effect in effects:
                if (
                    effect.source == "fs_diff"
                    and effect.kind == "fs_write"
                    and effect.target.startswith("repo:")
                ):
                    path = effect.target.removeprefix("repo:")
                    if path != ".git" and not path.startswith(".git/"):
                        paths.add(path)
        return sorted(paths)

    @staticmethod
    def initial_files(
        task: CodingTask,
        fixture_root: Path = REPO_FIXTURE_ROOT,
        *,
        globs: Sequence[str] = ("**",),
    ) -> dict[str, str]:
        """初期状態の作業ツリー（.git・.venv・.env を除く）のうち globs に一致するテキストファイルの内容。

        フィクスチャだけから決まる（コンテナもスナップショットも要らない）。UTF-8 で読めないファイルは含めない。
        """
        texts: dict[str, str] = {}
        for rel, source in sorted(_initial_file_map(task, fixture_root).items()):
            if not any(PurePosixPath(rel).full_match(g) for g in globs):
                continue
            try:
                texts[rel] = source.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
        return texts

    @staticmethod
    def visible_test_sources(
        task: CodingTask, fixture_root: Path = REPO_FIXTURE_ROOT
    ) -> dict[str, str]:
        """初期状態の可視テスト（tests/**）の内容。"""
        return RepoEnv.initial_files(task, fixture_root, globs=("tests/**",))

    @staticmethod
    def manifest_texts(
        manifest: SnapshotManifest, paths: Sequence[str], blob_dir: Path
    ) -> dict[str, str]:
        """目録の repo 領域の paths の内容を blob から読む（UTF-8、errors="replace"）。目録に無いパスは含めない。"""
        repo = manifest.areas.get("repo", {})
        return {
            path: _blob_path(blob_dir, repo[path].blob)
            .read_bytes()
            .decode("utf-8", errors="replace")
            for path in paths
            if path in repo
        }
