"""ジャッジの構造化出力（`submit_verdict` ツール ＋ tool_choice 強制）。

本文テキストを正規表現で解析しない。live が使えない場合は決定的な代替判定器
（`offline`）を使い、来歴を `simulated` として返す。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

from agenteval.llm.client import LLMClient, MessageRequest
from agenteval.llm.models import judge_request_defaults, model_id

JUDGE_TOOL: dict[str, Any] = {
    "name": "submit_verdict",
    "description": (
        "評価結果を提出する。score は 1〜5 の整数で、5 が最良。"
        "reason は判断の根拠を 1〜2 文で書く。evidence_refs には根拠になった"
        "ステップ番号や資源 id を入れる。推測ではなく提示された証拠だけを使うこと。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "score": {"type": "integer", "minimum": 1, "maximum": 5},
            "reason": {"type": "string"},
            "evidence_refs": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["score", "reason"],
    },
}


class Verdict(BaseModel):
    """ジャッジ 1 件の判定。"""

    score: int
    reason: str = ""
    evidence_refs: list[str] = Field(default_factory=list)
    provenance: str = "simulated"
    model: str = "-"


def ask(
    client: LLMClient | None,
    system: str,
    user: str,
    offline: Callable[[str], Verdict],
) -> Verdict:
    """ジャッジに 1 件問う。client が無い／sim モードなら決定的代替を使う。"""
    if client is None or client.mode == "sim":
        verdict = offline(user)
        verdict.provenance = "simulated"
        verdict.model = "offline-rubric"
        return verdict
    request = MessageRequest(
        model=model_id("judge"),
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": [{"type": "text", "text": user}]}],
        tools=[JUDGE_TOOL],
        tool_choice={"type": "tool", "name": "submit_verdict"},
        **judge_request_defaults(),
    )
    result = client.create(request)
    uses = result.tool_uses()
    if not uses:
        raise RuntimeError("ジャッジが submit_verdict を返さなかった")
    payload: dict[str, Any] = uses[0].get("input") or {}
    return Verdict(
        score=int(payload.get("score", 3)),
        reason=str(payload.get("reason", "")),
        evidence_refs=[str(x) for x in payload.get("evidence_refs", [])],
        provenance="live" if client.mode in ("auto", "record") else "replay",
        model=model_id("judge"),
    )
