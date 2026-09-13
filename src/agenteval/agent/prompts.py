"""system プロンプトの組み立て。末尾ブロックに cache_control を付ける。"""

from __future__ import annotations

from typing import Any

from agenteval.core.registry import Task, Version


def build_system(version: Version, task: Task) -> list[dict[str, Any]]:
    """system ブロック列。プロンプト接頭辞が毎ステップ同じになるようにする。"""
    blocks: list[dict[str, Any]] = [{"type": "text", "text": version.prompt_text()}]
    context = f"今日は {task_today(task)} です。利用者のメールアドレスは me@example.co.jp です。"
    blocks.append({"type": "text", "text": context, "cache_control": {"type": "ephemeral"}})
    return blocks


def task_today(_task: Task) -> str:
    """環境の固定日付（実時刻に依存させない）。"""
    from agenteval.env.fixtures import BASE_DATE

    return BASE_DATE


def build_user_message(task: Task) -> dict[str, Any]:
    """最初の user メッセージ。"""
    return {"role": "user", "content": [{"type": "text", "text": task.prompt}]}
