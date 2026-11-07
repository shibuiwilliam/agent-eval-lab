"""時間・数量の小さな補助関数。バケット・窓・バックオフが共有する。"""

from __future__ import annotations

import math


def elapsed(since: float, now: float) -> float:
    """since から now までの経過秒。時計が戻っていても負にはしない。"""
    return max(now - since, 0.0)


def clamp(value: float, lo: float, hi: float) -> float:
    """value を lo 以上 hi 以下に収める。"""
    return min(max(value, lo), hi)


def ceil_div(num: float, den: float) -> int:
    """num / den を整数に切り上げる。num が 0 以下なら 0。den が 0 以下なら ValueError。"""
    if den <= 0:
        raise ValueError("den は正でなければならない")
    if num <= 0:
        return 0
    return math.ceil(num / den)
