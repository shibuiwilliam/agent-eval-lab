"""タスク YAML と版 YAML の読み込み。版ハッシュもここで計算する。"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from agenteval.core.normalize import sha256_of
from agenteval.env.tools import Toolset, toolset_hash_material
from agenteval.llm.cost import REPO_ROOT
from agenteval.llm.models import Role, model_id

TASK_DIR = REPO_ROOT / "tasks"
VERSION_DIR = REPO_ROOT / "versions"
PROMPT_DIR = REPO_ROOT / "prompts"

SECTION_RE = re.compile(r"<!--\s*section:\s*([a-z0-9_]+)\s*-->")


class Milestone(BaseModel):
    """半順序マイルストーン 1 個。`after` が到達順序の制約。"""

    id: str
    check: dict[str, Any]
    after: list[str] = Field(default_factory=list)


class SimIntent(BaseModel):
    """シミュレート・エージェント用の意図（ADR-008）。acceptance とは独立に書く。

    acceptance はこの意図を見ない。意図どおりに動いても acceptance を満たすとは限らない
    （空きの無い時間帯を選ぶ、会議室を落とす、など）。
    """

    action: Literal[
        "create_event",
        "delete_event",
        "send_mail",
        "edit_file",
        "delete_file",
        "backup_file",
        "lookup_cost",
        "do_nothing",
        "ask",
        "recall",
    ]
    title: str = ""
    minutes: int = 30
    room: str | None = None
    day_offset: int = 7
    hour: int = 10
    attendee: str | None = None
    to: str | None = None
    subject: str = ""
    body: str = ""
    path: str = ""
    text: str = ""
    key: str = ""
    query: str = ""
    event_id: str = ""
    difficulty: float = 0.2


class Task(BaseModel):
    """タスク定義。"""

    id: str
    category: Literal["scheduling", "mail", "files", "mixed", "edge"]
    kind: Literal["normal", "do_nothing", "ambiguous", "niah", "trivial"] = "normal"
    prompt: str
    fixture: str = "office_small_v1"
    tags: list[str] = Field(default_factory=list)
    milestones: list[Milestone] = Field(default_factory=list)
    forbidden: list[dict[str, Any]] = Field(default_factory=list)
    acceptance: list[dict[str, Any]] = Field(default_factory=list)
    l_min: int = 3
    risk: Literal["normal", "inviolable"] = "normal"
    visibility: Literal["public", "private"] = "public"
    canary: str | None = None
    key_fact: str | None = None
    n_noise: int = 0
    sim: SimIntent


class VersionConfig(BaseModel):
    """版の設定。"""

    max_steps: int = 25
    max_tokens: int = 1024
    context_strategy: Literal["raw", "summarize"] = "raw"
    plan_first: bool = False
    summarize_after: int = 3


class Planted(BaseModel):
    """植込み欠陥の説明と期待効果。"""

    description: str = ""
    expected_effects: list[dict[str, Any]] = Field(default_factory=list)


class Version(BaseModel):
    """エージェントの版（model ＋ system prompt ＋ tools ＋ config）。"""

    id: str
    base: str | None = None
    model: Role = "agent"
    system_prompt: str
    toolset: Toolset = "v1"
    config: VersionConfig = Field(default_factory=VersionConfig)
    planted: Planted = Field(default_factory=Planted)
    quality_label: Literal["good", "bad", "neutral"] = "neutral"

    def prompt_text(self) -> str:
        """system prompt 本文。"""
        return (REPO_ROOT / self.system_prompt).read_text(encoding="utf-8")

    def sections(self) -> list[str]:
        """prompt 内の section マーカー id（3章のカバレッジが使う）。"""
        return SECTION_RE.findall(self.prompt_text())

    def section_bodies(self) -> dict[str, str]:
        """section id → 本文。プロンプト差分の粒度。"""
        text = self.prompt_text()
        out: dict[str, str] = {}
        marks = list(SECTION_RE.finditer(text))
        for i, m in enumerate(marks):
            end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
            out[m.group(1)] = text[m.end() : end].strip()
        return out

    def model_id(self) -> str:
        return model_id(self.model)

    def hash(self) -> str:
        """版ハッシュ = model_id ＋ prompt 本文 ＋ ツール schema ＋ config。"""
        return sha256_of(
            [
                self.model_id(),
                self.prompt_text(),
                toolset_hash_material(self.toolset),
                self.config.model_dump(),
            ]
        )


def _load_yaml(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data


@lru_cache(maxsize=1)
def load_tasks() -> dict[str, Task]:
    """tasks/*.yaml を全部読む。"""
    out: dict[str, Task] = {}
    for path in sorted(TASK_DIR.glob("*.yaml")):
        task = Task.model_validate(_load_yaml(path))
        out[task.id] = task
    return out


@lru_cache(maxsize=1)
def load_versions() -> dict[str, Version]:
    """versions/*.yaml を全部読む。"""
    out: dict[str, Version] = {}
    for path in sorted(VERSION_DIR.glob("*.yaml")):
        version = Version.model_validate(_load_yaml(path))
        out[version.id] = version
    return out


def get_task(task_id: str) -> Task:
    tasks = load_tasks()
    if task_id not in tasks:
        raise KeyError(f"未知のタスク: {task_id}")
    return tasks[task_id]


def get_version(version_id: str) -> Version:
    versions = load_versions()
    if version_id not in versions:
        raise KeyError(f"未知の版: {version_id}")
    return versions[version_id]
