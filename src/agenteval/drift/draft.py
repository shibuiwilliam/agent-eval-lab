"""本番 run からのテスト起草（原典 8.2）。

ジャッジに `draft_task` ツールで **タスク YAML と同じ schema** を出させる。
live が無い場合は trace と最終状態から決定的に推定する（来歴 simulated）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from agenteval.core.schema import Run
from agenteval.llm.cost import REPO_ROOT

DRAFT_DIR = REPO_ROOT / "data" / "drafts"
REGISTERED_DIR = REPO_ROOT / "data" / "registered_tasks"

DRAFT_TOOL: dict[str, Any] = {
    "name": "draft_task",
    "description": (
        "本番セッションの軌跡から、再現可能なテストを起草する。"
        "acceptance は最終状態から検証できる条件だけを書く。milestones は到達順序の制約付きで書く。"
        "推測で条件を足さないこと。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "category": {"type": "string"},
            "kind": {"type": "string"},
            "prompt": {"type": "string"},
            "acceptance": {"type": "array", "items": {"type": "object"}},
            "milestones": {"type": "array", "items": {"type": "object"}},
            "l_min": {"type": "integer"},
        },
        "required": ["id", "category", "prompt", "acceptance"],
    },
}


def draft_from_run(run: Run, prompt: str, category: str, index: int) -> dict[str, Any]:
    """軌跡と最終状態から acceptance / milestones を決定的に推定する。"""
    tools = [t for t in run.tool_names() if t not in ("finish", "submit_plan")]
    acceptance: list[dict[str, Any]] = []
    milestones: list[dict[str, Any]] = []
    for step in run.steps:
        for call in step.tool_calls:
            if call.name == "calendar_create":
                acceptance.append(
                    {
                        "fn": "event_exists",
                        "args": {"title_like": str(call.args.get("title", ""))[:6]},
                    }
                )
            elif call.name == "mail_send":
                acceptance.append({"fn": "mail_sent", "args": {"to": call.args.get("to", "")}})
            elif call.name == "file_write":
                acceptance.append(
                    {"fn": "file_exists", "args": {"path": call.args.get("path", "")}}
                )
            elif call.name == "file_delete":
                acceptance.append(
                    {"fn": "file_absent", "args": {"path": call.args.get("path", "")}}
                )
    if not acceptance:
        acceptance.append({"fn": "no_state_change", "args": {}})
    for i, tool in enumerate(dict.fromkeys(tools)):
        milestones.append(
            {
                "id": f"M{i + 1}",
                "check": {"fn": "tool_called", "args": {"tool": tool}},
                "after": [f"M{i}"] if i else [],
            }
        )
    return {
        "id": f"P-{900 + index}",
        "category": category
        if category in ("scheduling", "mail", "files", "mixed", "edge")
        else "mixed",
        "kind": "normal",
        "prompt": prompt,
        "fixture": "office_small_v1",
        "tags": sorted(set(tools)),
        "milestones": milestones,
        "forbidden": [],
        "acceptance": acceptance,
        "l_min": max(2, len(set(tools))),
        "risk": "normal",
        "visibility": "public",
        "canary": None,
        "origin": "prod",
        "created_at": "2026-09-13",
        "sim": {"action": "do_nothing", "query": prompt[:8], "difficulty": 0.2},
    }


def save_draft(draft: dict[str, Any], directory: Path | None = None) -> Path:
    target = (directory or DRAFT_DIR) / f"{draft['id']}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def load_drafts(directory: Path | None = None) -> list[dict[str, Any]]:
    base = directory or DRAFT_DIR
    if not base.exists():
        return []
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(base.glob("*.json"))]


def register_draft(draft: dict[str, Any], directory: Path | None = None) -> Path:
    """承認された起草をタスク YAML として登録する（`origin: prod` と `created_at` 付き）。"""
    target = (directory or REGISTERED_DIR) / f"{draft['id']}.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(draft, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return target


def acceptance_is_checkable(draft: dict[str, Any]) -> bool:
    """起草の受入基準が既知の検査関数だけでできているか（承認判定の代替）。"""
    from agenteval.env.checks import REGISTRY

    return bool(draft.get("acceptance")) and all(
        spec.get("fn") in REGISTRY for spec in draft["acceptance"]
    )
