"""コーディングの run の保存と読み込み（docs/plan/coding/SPEC.md 2.2・11.1 節、ADR-039・040）。

置き場は `data/coding/{traces,outcomes,canaries}/<mode>/<run_id>.json`。mode の階層で live / replay / sim を分け、
互いの生成物を上書きしない。予算停止などで途中終了した run は `.partial.<n>.json` に書き、読み込みの対象外にする。
書き込みは「一時ファイル ＋ os.replace」で、読み手が途中のファイルを見ることはない。
"""

from __future__ import annotations

import os
import re
import uuid
from collections.abc import Collection, Sequence
from pathlib import Path

from pydantic import TypeAdapter

from agenteval.core.coding_registry import mode_dir_of, parse_coding_run_id
from agenteval.core.coding_types import CODING_DATA_ROOT, CodingOutcome, ModeDir
from agenteval.core.schema import Run
from agenteval.security.canary import CanaryFingerprint

# 途中終了の run は `<run_id>.partial.<n>.json`（n は 0 からの連番。やり直しで前回の記録を失わない）
PARTIAL_NAME = re.compile(r"\.partial(\.[0-9]+)?\.json$")

_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_FINGERPRINTS = TypeAdapter(list[CanaryFingerprint])


def _check_run_id(run_id: str) -> None:
    """run_id をファイル名に使うので、区切りや .. を含むものを拒否する。"""
    if not _RUN_ID_RE.match(run_id) or run_id in (".", ".."):
        raise ValueError(f"run_id がファイル名に使えない形式: {run_id!r}")


def _create_exclusive(path: Path, text: str) -> None:
    """O_EXCL で新規作成して書く（既存なら FileExistsError。os.link が使えない FS 向けの代替）。"""
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        raise FileExistsError(f"既にある（上書きしない）: {path}") from None
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
    except BaseException:
        path.unlink(missing_ok=True)  # 書きかけを残さない
        raise


def _atomic_write(path: Path, text: str, *, exclusive: bool = False) -> None:
    """一時ファイルに書いて公開する。

    exclusive=False: os.replace（既存のファイルは置き換える）。
    exclusive=True: os.link で公開する（既存のファイルがあれば FileExistsError。確認と公開の間の競合に強い）。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        if exclusive:
            try:
                os.link(tmp, path)
            except FileExistsError:
                raise FileExistsError(f"既にある（上書きしない）: {path}") from None
            except OSError:
                # os.link に対応しない FS。O_EXCL での作成（既存なら FileExistsError）に切り替える
                _create_exclusive(path, text)
        else:
            os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def trace_path(run_id: str, mode_dir: ModeDir, *, data_root: Path = CODING_DATA_ROOT) -> Path:
    """`traces/<mode>/<run_id>.json`。"""
    _check_run_id(run_id)
    return data_root / "traces" / mode_dir / f"{run_id}.json"


def partial_trace_path(
    run_id: str, mode_dir: ModeDir, n: int = 0, *, data_root: Path = CODING_DATA_ROOT
) -> Path:
    """`traces/<mode>/<run_id>.partial.<n>.json`（ADR-040。n は連番）。"""
    _check_run_id(run_id)
    if n < 0:
        raise ValueError(f"n は 0 以上: {n}")
    return data_root / "traces" / mode_dir / f"{run_id}.partial.{n}.json"


def partial_trace_paths(
    run_id: str, mode_dir: ModeDir, *, data_root: Path = CODING_DATA_ROOT
) -> list[Path]:
    """既にある partial の trace を連番順に。"""
    _check_run_id(run_id)
    directory = data_root / "traces" / mode_dir
    found: list[tuple[int, Path]] = []
    for path in directory.glob(f"{run_id}.partial.*.json"):
        middle = path.name.removeprefix(f"{run_id}.partial.").removesuffix(".json")
        if middle.isdigit():
            found.append((int(middle), path))
    return [path for _, path in sorted(found)]


def outcome_path(run_id: str, mode_dir: ModeDir, *, data_root: Path = CODING_DATA_ROOT) -> Path:
    """`outcomes/<mode>/<run_id>.json`。"""
    _check_run_id(run_id)
    return data_root / "outcomes" / mode_dir / f"{run_id}.json"


def fingerprint_path(run_id: str, mode_dir: ModeDir, *, data_root: Path = CODING_DATA_ROOT) -> Path:
    """`canaries/<mode>/<run_id>.json`。"""
    _check_run_id(run_id)
    return data_root / "canaries" / mode_dir / f"{run_id}.json"


def save_coding_run(
    run: Run,
    outcome: CodingOutcome | None,
    *,
    partial: bool = False,
    data_root: Path = CODING_DATA_ROOT,
) -> Path:
    """Run（と CodingOutcome）を保存して trace のパスを返す。

    partial=False: traces/<mode>/<run_id>.json と（outcome があれば）outcomes/<mode>/<run_id>.json を書く。
      同じ run_id の再実行は上書きする（mode の階層が違えば別のファイル）。
    partial=True: traces/<mode>/<run_id>.partial.<n>.json に書く（ADR-040。予算停止などの途中終了）。
      n は既存の partial の次の連番で、やり直しても前回の記録を上書きしない。outcome は渡せない（途中の run に結果は無い）。
    """
    mode_dir = mode_dir_of(run.mode)
    if partial:
        if outcome is not None:
            raise ValueError("partial の run に outcome は保存しない")
        existing = partial_trace_paths(run.run_id, mode_dir, data_root=data_root)
        n = 0
        if existing:
            n = int(existing[-1].name.removeprefix(f"{run.run_id}.partial.").split(".")[0]) + 1
        while True:
            path = partial_trace_path(run.run_id, mode_dir, n, data_root=data_root)
            try:
                _atomic_write(path, run.model_dump_json(indent=2), exclusive=True)
            except FileExistsError:
                n += 1  # 並行して同じ run_id の partial を書いた別プロセスがいても上書きしない
                continue
            return path
    if outcome is not None and outcome.run_id != run.run_id:
        raise ValueError(f"outcome の run_id が違う: {outcome.run_id} != {run.run_id}")
    path = trace_path(run.run_id, mode_dir, data_root=data_root)
    _atomic_write(path, run.model_dump_json(indent=2))
    if outcome is not None:
        _atomic_write(
            outcome_path(run.run_id, mode_dir, data_root=data_root),
            outcome.model_dump_json(indent=2),
        )
    return path


def load_coding_runs(
    mode_dir: ModeDir,
    *,
    version_ids: Collection[str] | None = None,
    stages: Collection[str] | None = None,
    data_root: Path = CODING_DATA_ROOT,
) -> list[Run]:
    """`traces/<mode>/*.json` を run_id 順に読む。`.partial.<n>.json` は読まない（ADR-040）。

    version_ids / stages を渡せば run_id の部品で絞る（パイロットとコーパスを混ぜない）。
    絞り込みを指定したとき、run_id がコーディングの形式でないファイルは除く。
    """
    directory = data_root / "traces" / mode_dir
    if not directory.is_dir():
        return []
    runs: list[Run] = []
    for path in sorted(directory.glob("*.json")):
        if PARTIAL_NAME.search(path.name):
            continue
        if version_ids is not None or stages is not None:
            try:
                key = parse_coding_run_id(path.stem)
            except ValueError:
                continue
            if version_ids is not None and key.version_id not in version_ids:
                continue
            if stages is not None and key.stage not in stages:
                continue
        runs.append(Run.model_validate_json(path.read_text(encoding="utf-8")))
    return runs


def load_coding_outcome(
    run_id: str, mode_dir: ModeDir, *, data_root: Path = CODING_DATA_ROOT
) -> CodingOutcome:
    """`outcomes/<mode>/<run_id>.json` を読む。無ければ FileNotFoundError。"""
    path = outcome_path(run_id, mode_dir, data_root=data_root)
    return CodingOutcome.model_validate_json(path.read_text(encoding="utf-8"))


def save_fingerprints(
    run_id: str,
    mode_dir: ModeDir,
    fingerprints: Sequence[CanaryFingerprint],
    *,
    data_root: Path = CODING_DATA_ROOT,
) -> Path:
    """カナリアの指紋（sha256 と長さだけ。値は持たない）を保存する。後から trace を走査し直す秘匿検査用。"""
    path = fingerprint_path(run_id, mode_dir, data_root=data_root)
    _atomic_write(path, _FINGERPRINTS.dump_json(list(fingerprints), indent=2).decode("utf-8"))
    return path


def load_fingerprints(
    run_id: str, mode_dir: ModeDir, *, data_root: Path = CODING_DATA_ROOT
) -> list[CanaryFingerprint]:
    """`save_fingerprints` の逆。無ければ FileNotFoundError。"""
    path = fingerprint_path(run_id, mode_dir, data_root=data_root)
    return _FINGERPRINTS.validate_json(path.read_text(encoding="utf-8"))
