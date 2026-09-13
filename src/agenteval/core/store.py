"""軌跡の保存と読み出し。`data/traces/<run_id>.json`。"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from agenteval.core.schema import Run
from agenteval.llm.cost import REPO_ROOT

TRACE_DIR = REPO_ROOT / "data" / "traces"


def save_run(run: Run, directory: Path | None = None) -> Path:
    """1 run を保存する。"""
    target = (directory or TRACE_DIR) / f"{run.run_id}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(run.model_dump_json(indent=2), encoding="utf-8")
    return target


def load_run(run_id: str, directory: Path | None = None) -> Run:
    path = (directory or TRACE_DIR) / f"{run_id}.json"
    return Run.model_validate_json(path.read_text(encoding="utf-8"))


def iter_runs(directory: Path | None = None) -> Iterator[Run]:
    """保存済み run を全部読む。"""
    base = directory or TRACE_DIR
    if not base.exists():
        return
    for path in sorted(base.glob("*.json")):
        yield Run.model_validate_json(path.read_text(encoding="utf-8"))


def load_corpus(directory: Path | None = None) -> list[Run]:
    """コーパス全体（保存順ではなく run_id 順）。"""
    return list(iter_runs(directory))


def trace_hash(run: Run) -> str:
    """再生の一致確認に使う軌跡ハッシュ。実時刻・レイテンシ・パスは除外する。"""
    from agenteval.core.normalize import sha256_of

    material = [
        run.task_id,
        run.version_id,
        run.version_hash,
        [
            {
                "i": s.i,
                "before": s.state_hash_before,
                "after": s.state_hash_after,
                "calls": [{"name": c.name, "args": c.args_norm} for c in s.tool_calls],
                "results": [{"content": r.content, "is_error": r.is_error} for r in s.tool_results],
                "text": s.assistant_text,
            }
            for s in run.steps
        ],
        run.final_text,
        run.claims,
        run.outcome.model_dump() if run.outcome else None,
    ]
    return sha256_of(material)


def dump_json(obj: object, path: Path) -> Path:
    """結果 JSON の保存（実験スクリプト用）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
