"""汚染検知（原典 8.9）。カナリア走査と public/private 合格率差。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from agenteval.core.registry import get_task, load_tasks
from agenteval.core.schema import Run
from agenteval.llm.cost import REPO_ROOT

# 走査対象（.claude/rules/drift.md）
SCAN_DIRS = ("prompts", "versions", "docs")
SCAN_CASSETTE_DIR = "data/cassettes/llm"


@dataclass
class ScanHit:
    """カナリア検出 1 件。"""

    canary: str
    task_id: str
    path: str


@dataclass
class ScanResult:
    """走査結果。"""

    hits: list[ScanHit] = field(default_factory=list)
    scanned_files: int = 0

    @property
    def detected_tasks(self) -> set[str]:
        return {h.task_id for h in self.hits}


def canaries() -> dict[str, str]:
    """private タスクの canary 文字列 → タスク ID。"""
    return {t.canary: t.id for t in load_tasks().values() if t.canary}


def scan(root: Path | None = None, versions: list[str] | None = None) -> ScanResult:
    """`prompts/`・`versions/`・`docs/`・LLM カセットの system 部分を走査する。"""
    base = root or REPO_ROOT
    targets: list[Path] = []
    for directory in SCAN_DIRS:
        path = base / directory
        if path.exists():
            targets.extend(
                p for p in path.rglob("*") if p.is_file() and p.suffix in (".md", ".yaml", ".json")
            )
    cassettes = base / SCAN_CASSETTE_DIR
    if cassettes.exists():
        targets.extend(cassettes.rglob("*.json"))
    known = canaries()
    result = ScanResult(scanned_files=len(targets))
    for path in targets:
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for canary, task_id in known.items():
            if canary in text and not _is_task_definition(path, task_id):
                if versions is not None and not any(v in str(path) for v in versions):
                    continue
                result.hits.append(
                    ScanHit(canary=canary, task_id=task_id, path=str(path.relative_to(base)))
                )
    return result


def _is_task_definition(path: Path, task_id: str) -> bool:
    """タスク定義そのもの（tasks/T-xxx.yaml）は汚染ではない。"""
    return path.name.startswith(task_id)


def scan_version(version_id: str, root: Path | None = None) -> ScanResult:
    """1 つの版のプロンプトだけを走査する。"""
    from agenteval.core.registry import get_version

    base = root or REPO_ROOT
    version = get_version(version_id)
    path = base / version.system_prompt
    text = path.read_text(encoding="utf-8")
    result = ScanResult(scanned_files=1)
    for canary, task_id in canaries().items():
        if canary in text:
            result.hits.append(ScanHit(canary=canary, task_id=task_id, path=version.system_prompt))
    return result


def visibility_gap(runs: list[Run], version_id: str) -> dict[str, float]:
    """版ごとの public / private 合格率差。"""
    subset = [r for r in runs if r.version_id == version_id]
    public = [r for r in subset if get_task(r.task_id).visibility == "public"]
    private = [r for r in subset if get_task(r.task_id).visibility == "private"]
    public_rate = sum(r.passed() for r in public) / len(public) if public else 0.0
    private_rate = sum(r.passed() for r in private) / len(private) if private else 0.0
    return {
        "public": round(public_rate, 4),
        "private": round(private_rate, 4),
        "gap": round(private_rate - public_rate, 4),
        "n_public": len(public),
        "n_private": len(private),
    }
