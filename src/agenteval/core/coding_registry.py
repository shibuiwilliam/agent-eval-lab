"""コーディング用のタスク・版・タスク集合のローダーと識別子（docs/plan/coding/SPEC.md 2.4・7・11.1 節、W1-I5）。

既存の `core/registry.py`（オフィス用）とは別のローダーにする（ADR-039、K8）。読むのは `coding/` だけで、
オフィス側の `tasks/`・`versions/`・`prompts/` と版ハッシュは変えない。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, NamedTuple

import yaml

from agenteval.core.coding_types import (
    CODING_TASK_DIR,
    CODING_VERSION_DIR,
    SCRIPT_ID_PATTERN,
    STAGE_PATTERN,
    TASK_ID_PATTERN,
    TASK_SETS_PATH,
    UNIMPLEMENTED_HARNESS_FIELDS,
    VERSION_ID_PATTERN,
    CodingTask,
    CodingVersion,
    HarnessConfig,
    ModeDir,
)
from agenteval.core.normalize import sha256_of
from agenteval.core.schema import RunMode
from agenteval.env import repo_tools
from agenteval.llm.cost import REPO_ROOT
from agenteval.llm.models import model_id

VERSION_HASH_TAG = "agenteval.coding.version/v1"

_STAGE_RE = re.compile(STAGE_PATTERN)
_TASK_ID_RE = re.compile(TASK_ID_PATTERN)
_VERSION_ID_RE = re.compile(VERSION_ID_PATTERN)
_SCRIPT_ID_RE = re.compile(SCRIPT_ID_PATTERN)
_REPEAT_RE = re.compile(r"^r(0|[1-9][0-9]*)$")


# --- YAML の読み込み ------------------------------------------------------------
def _load_yaml(path: Path) -> Any:  # Any: YAML の任意の木。呼び出し側が pydantic で検証する
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_coding_tasks(directory: Path = CODING_TASK_DIR) -> dict[str, CodingTask]:
    """`coding/tasks/*.yaml` を全部読む。ディレクトリが無ければ空。

    ファイル名（拡張子を除く）とタスク ID が違う、または ID が重複するものは ValueError。
    """
    out: dict[str, CodingTask] = {}
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob("*.yaml")):
        task = CodingTask.model_validate(_load_yaml(path))
        if task.id != path.stem:
            raise ValueError(f"{path}: タスク ID {task.id!r} がファイル名と違う")
        if task.id in out:
            raise ValueError(f"タスク ID が重複: {task.id}")
        out[task.id] = task
    return out


def _check_harness(version: CodingVersion) -> None:
    """未実装のハーネス部品に既定値以外が書かれた版を拒否する（ADR-035 判断 3）。

    効かない植込みを黙って作らないため。egress は "deny" だけを実装している。
    """
    defaults = HarnessConfig()
    for name in UNIMPLEMENTED_HARNESS_FIELDS:
        if getattr(version.harness, name) != getattr(defaults, name):
            raise ValueError(
                f"{version.id}: harness.{name} は第 1 段で未実装。既定値"
                f" {getattr(defaults, name)!r} 以外は書けない"
            )


def load_coding_versions(directory: Path = CODING_VERSION_DIR) -> dict[str, CodingVersion]:
    """`coding/versions/*.yaml` を全部読む。ディレクトリが無ければ空。

    ValueError: 未実装のハーネス部品（UNIMPLEMENTED_HARNESS_FIELDS）に既定値以外を書いた版、
    egress が "deny" でない版、ファイル名と版 ID が違う・重複する版。
    FileNotFoundError: system_prompt のファイルが無い版（REPO_ROOT からの相対パス。絶対パスも可）。
    """
    out: dict[str, CodingVersion] = {}
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob("*.yaml")):
        version = CodingVersion.model_validate(_load_yaml(path))
        if version.id != path.stem:
            raise ValueError(f"{path}: 版 ID {version.id!r} がファイル名と違う")
        if version.id in out:
            raise ValueError(f"版 ID が重複: {version.id}")
        _check_harness(version)
        prompt_path = REPO_ROOT / version.system_prompt
        if not prompt_path.is_file():
            raise FileNotFoundError(f"{version.id}: system_prompt が無い: {prompt_path}")
        out[version.id] = version
    return out


def load_task_sets(
    path: Path = TASK_SETS_PATH,
) -> dict[str, Any]:  # Any: 7.4 節の YAML（リストと辞書が混在）
    """`coding/task_sets.yaml` を読む。トップレベルが辞書でなければ ValueError。"""
    data = _load_yaml(path)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: トップレベルは辞書でなければならない")  # noqa: TRY004
    return {str(k): v for k, v in data.items()}


def get_coding_task(task_id: str) -> CodingTask:
    """タスク ID から取る（未知なら KeyError）。"""
    tasks = load_coding_tasks()
    if task_id not in tasks:
        raise KeyError(f"未知のコーディングタスク: {task_id}")
    return tasks[task_id]


def get_coding_version(version_id: str) -> CodingVersion:
    """版 ID から取る（未知なら KeyError）。"""
    versions = load_coding_versions()
    if version_id not in versions:
        raise KeyError(f"未知のコーディング版: {version_id}")
    return versions[version_id]


# --- 版ハッシュ（SPEC 7.3 節） ---------------------------------------------------
def prompt_text(version: CodingVersion) -> str:
    """system プロンプトの本文（`coding/prompts/<name>.md`）。"""
    return (REPO_ROOT / version.system_prompt).read_text(encoding="utf-8")


def version_hash_material(version: CodingVersion) -> list[Any]:  # Any: 正規 JSON にする任意の値
    """版ハッシュの材料。`planted`・`quality_label`・`base`・`id` は入れない（オフィスと同じ）。

    harness は既定値でない項目だけを入れる（`exclude_defaults=True`）。第 2 段以降で既定 off の部品を
    足しても既存の版のハッシュが変わらない（K8 の轍を踏まない）。
    """
    return [
        VERSION_HASH_TAG,
        model_id(version.model),
        prompt_text(version),
        repo_tools.toolset_hash_material(version.toolset),
        version.harness.model_dump(mode="json", exclude_defaults=True),
    ]


def version_hash(version: CodingVersion) -> str:
    """版ハッシュ（model_id ＋ プロンプト本文 ＋ ツール schema ＋ 既定値でないハーネス設定）。"""
    return sha256_of(version_hash_material(version))


# --- run_id・段・塩（SPEC 2.4 節） -------------------------------------------------
class CodingRunKey(NamedTuple):
    """`parse_coding_run_id` の結果。"""

    task_id: str
    version_id: str
    stage: str
    repeat: int
    script_id: str | None


def coding_run_id(
    task_id: str, version_id: str, stage: str, repeat: int, script_id: str | None = None
) -> str:
    """`cd__<task>__<version>__<stage>__r<repeat>[__<script>]`。形式に合わない部品は ValueError。

    run_id に mode も seed も入れない（mode はディレクトリで、seed は `Run.seed` で持つ）。
    """
    if not _TASK_ID_RE.match(task_id):
        raise ValueError(f"タスク ID の形式が違う: {task_id!r}")
    if not _VERSION_ID_RE.match(version_id):
        raise ValueError(f"版 ID の形式が違う: {version_id!r}")
    if not _STAGE_RE.match(stage):
        raise ValueError(f"段の形式が違う: {stage!r}")
    if repeat < 0:
        raise ValueError(f"repeat は 0 以上: {repeat}")
    if script_id is not None and not _SCRIPT_ID_RE.match(script_id):
        raise ValueError(f"台本 ID の形式が違う: {script_id!r}")
    parts = ["cd", task_id, version_id, stage, f"r{repeat}"]
    if script_id is not None:
        parts.append(script_id)
    return "__".join(parts)


def parse_coding_run_id(run_id: str) -> CodingRunKey:
    """`coding_run_id` の逆。形式に合わなければ ValueError。"""
    parts = run_id.split("__")
    if len(parts) not in (5, 6) or parts[0] != "cd":
        raise ValueError(f"コーディングの run_id の形式が違う: {run_id!r}")
    _, task_id, version_id, stage, repeat_text, *rest = parts
    match = _REPEAT_RE.match(repeat_text)
    if match is None:
        raise ValueError(f"run_id の repeat 部が違う: {run_id!r}")
    script_id = rest[0] if rest else None
    key = CodingRunKey(task_id, version_id, stage, int(repeat_text[1:]), script_id)
    # 再構成して正規形と一致することで、各部品の形式も同時に検査する
    if coding_run_id(*key) != run_id:
        raise ValueError(f"コーディングの run_id の形式が違う: {run_id!r}")
    return key


def coding_cassette_salt(task_id: str, version_id: str, seed: int, repeat: int) -> str:
    """カセットの塩 `cd:<task>:<version>:s<seed>:r<repeat>`（ADR-038 判断 3）。塩は API に送らない。"""
    return f"cd:{task_id}:{version_id}:s{seed}:r{repeat}"


def mode_dir_of(mode: RunMode) -> ModeDir:
    """`Run.mode` から生成物のディレクトリ名を決める。branch は第 1 段で使わないので ValueError。"""
    if mode == "branch":
        raise ValueError("mode='branch' はコーディングの生成物の階層を持たない")
    return mode
