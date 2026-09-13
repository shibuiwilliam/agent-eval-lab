"""テストピラミッドの段判定（原典 3.5）。"""

from __future__ import annotations

from typing import Literal

from agenteval.core.schema import Change

Level = Literal["L0", "L1", "L2", "L3", "L4", "L5"]
ORDER: list[Level] = ["L0", "L1", "L2", "L3", "L4", "L5"]

# 変更の種類 → 最低限走らせるべき段。
# L0 静的検査 / L1 ツール単体 / L2 短い軌跡 / L3 完全な軌跡 / L4 長期・多様 / L5 本番近似
LEVEL_BY_KIND: dict[str, Level] = {
    "code": "L1",
    "config": "L2",
    "prompt": "L2",
    "fixture": "L3",
    "tool": "L3",
    "model": "L4",
}


def decide_level(change: Change) -> Level:
    """変更分類から走らせる段を決める。"""
    return LEVEL_BY_KIND.get(change.kind, "L3")


def level_index(level: Level) -> int:
    return ORDER.index(level)


def at_least(decided: Level, observed: Level) -> bool:
    """判定した段が、反転が観測された最小の段以上か。"""
    return level_index(decided) >= level_index(observed)


def observed_min_level(flipped: bool, kind: str) -> Level:
    """反転が観測された最小の段（この例示環境では軌跡レベルでしか観測できない）。

    反転が無ければ L1 で止まる。
    """
    if not flipped:
        return "L1"
    return LEVEL_BY_KIND.get(kind, "L3")
