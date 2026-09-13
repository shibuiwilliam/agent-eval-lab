"""コンテキスト戦略。raw と summarize（k ステップより古い tool_result を要約に置換）。

置換前の生応答は trace に残す（Step.tool_results は常に生のまま）。
"""

from __future__ import annotations

import copy
from typing import Any


def apply_strategy(
    messages: list[dict[str, Any]], strategy: str, summarize_after: int
) -> list[dict[str, Any]]:
    """会話に戦略を適用した「送信用」の messages を返す。元の列は変更しない。"""
    if strategy != "summarize":
        return messages
    out = copy.deepcopy(messages)
    tool_result_positions = [
        i
        for i, m in enumerate(out)
        if any(b.get("type") == "tool_result" for b in m.get("content", []))
    ]
    to_summarize = tool_result_positions[: max(0, len(tool_result_positions) - summarize_after)]
    for i in to_summarize:
        for block in out[i]["content"]:
            if block.get("type") != "tool_result":
                continue
            raw = _block_text(block)
            block["content"] = [{"type": "text", "text": summarize_text(raw)}]
    return out


def _block_text(block: dict[str, Any]) -> str:
    content = block.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(c.get("text", "") for c in content if isinstance(c, dict))
    return ""


def summarize_text(text: str, keep: int = 160) -> str:
    """要約 = 先頭 keep 文字 ＋ 省略記号。情報を落とすことは織り込み済み（4.9 / 原典 4.11）。"""
    if len(text) <= keep:
        return text
    return text[:keep] + f"…（{len(text) - keep} 文字省略）"


def estimate_tokens(messages: list[dict[str, Any]], system: Any = None) -> int:
    """文字数からトークン数を概算する（日本語 1 文字 ≒ 1 トークンで近似）。"""
    total = 0
    if isinstance(system, list):
        total += sum(len(b.get("text", "")) for b in system)
    elif isinstance(system, str):
        total += len(system)
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            total += len(content)
            continue
        for block in content or []:
            if block.get("type") == "text":
                total += len(block.get("text", ""))
            elif block.get("type") == "tool_use":
                total += len(str(block.get("input", "")))
            elif block.get("type") == "tool_result":
                total += len(_block_text(block))
    return total // 2 + 1
