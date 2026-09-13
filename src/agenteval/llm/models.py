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

# live で確認済み（2026-09-13、open-questions の 1 件目の回答）:
#   - `strict: true` は Haiku 4.5 で受け付けられる。ただし入れ子の object（`$defs` の中を含む）
#     すべてに `additionalProperties: false` が必要で、1 つでも欠けると 400
#     `For 'object' type, 'additionalProperties' must be explicitly set to false`
#   - 個別のツール（file_read / submit_plan）は strict で通るが、13 ツールを同時に strict にすると
#     400 `Schema is too complex.` になる
#   - さらにこのプロジェクトでは strict を使うと v06_toolschema_v2 の植込み（誤った引数名を出して
#     エラーから復帰する挙動）が API 側で抑止され、観測対象そのものが消える
# 以上より既定は False。引数検証は pydantic 側で行い、失敗は tool_result の is_error で返す。
USE_STRICT_TOOLS = False

# live で確認済み（2026-09-13）: `output_config.effort` は Sonnet 5 では通るが、
# Haiku 4.5 では 400 `This model does not support the effort parameter.` になる。
# 被験エージェント（Haiku）には付けない。ジャッジ（Sonnet 5）にだけ付ける。
EFFORT_SUPPORTED: dict[str, bool] = {
    "claude-haiku-4-5-20251001": False,
    "claude-sonnet-5": True,
}


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
    """ジャッジ呼び出しの既定。

    ジャッジは Sonnet 5 なので `output_config.effort` が使える。判定は短い構造化出力なので
    `low` にしてコストを抑える（`effort` は GA でベータヘッダ不要）。
    """
    return {"max_tokens": 1024, "output_config": {"effort": "low"}}


def supports_effort(model: str) -> bool:
    """そのモデルが `output_config.effort` を受け付けるか。"""
    return EFFORT_SUPPORTED.get(model, False)
