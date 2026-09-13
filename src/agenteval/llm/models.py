"""モデル ID とロールの対応。モデル ID はこのファイルにだけ書く（CLAUDE.md）。"""

from __future__ import annotations

from typing import Any, Literal

Role = Literal["agent", "judge", "ref", "agent_swap"]

# 被験エージェント = Haiku 4.5（安価で失敗が出やすい）、ジャッジ／参照方策 = Sonnet 5。
# E8-6 / E3-5 のモデル入替実験では agent_swap を使う。
MODEL_IDS: dict[Role, str] = {
    "agent": "claude-haiku-4-5-20251001",
    "judge": "claude-sonnet-5",
    "ref": "claude-sonnet-5",
    "agent_swap": "claude-sonnet-5",
}

# open-questions: Haiku 4.5 で strict ツール定義が受け付けられるかは P0 で未確認
# （live 実行ができないため）。既定は False にし、pydantic 側で検証する。
USE_STRICT_TOOLS = False


def model_id(role: Role) -> str:
    """ロール名 → モデル ID。"""
    return MODEL_IDS[role]


def role_of(model: str) -> Role | None:
    """モデル ID → ロール（最初に一致したもの）。"""
    for role, mid in MODEL_IDS.items():
        if mid == model:
            return role
    return None


def judge_request_defaults() -> dict[str, Any]:
    """ジャッジ呼び出しの既定。effort の API 形式が確定するまでは max_tokens のみ。

    open-questions.md: effort パラメータの正確な形式は公式 docs で確認してから
    ここに集約する。現時点では付けない（400 を避ける）。
    """
    return {"max_tokens": 1024}
