"""再試行の待ち時間（指数バックオフ）。"""

from __future__ import annotations

from collections.abc import Callable

from throttlebox.units import clamp

Jitter = Callable[[float], float]


def _retry_delay(attempt: int, base: float, factor: float, cap: float) -> float:
    """attempt（0 始まり）回目の待ち時間。base * factor**attempt を cap で頭打ちにする。"""
    return clamp(base * factor**attempt, 0.0, cap)


def next_delay(attempt: int, base: float = 0.5, factor: float = 2.0, cap: float = 30.0) -> float:
    """attempt 回目（0 始まり）1 回分の待ち時間。負なら ValueError。"""
    if attempt < 0:
        raise ValueError("attempt は 0 以上でなければならない")
    return _retry_delay(attempt, base, factor, cap)


def backoff_delays(
    attempts: int,
    base: float = 0.5,
    factor: float = 2.0,
    cap: float = 30.0,
    jitter: Jitter | None = None,
) -> list[float]:
    """attempts 回分の待ち時間の並び。jitter があれば掛けて、結果も 0 以上 cap 以下に収める。"""
    delays: list[float] = []
    for i in range(attempts):
        delay = _retry_delay(i, base, factor, cap)
        if jitter is not None:
            delay = clamp(jitter(delay), 0.0, cap)
        delays.append(delay)
    return delays


def total_wait(
    attempts: int,
    base: float = 0.5,
    factor: float = 2.0,
    cap: float = 30.0,
    jitter: Jitter | None = None,
) -> float:
    """backoff_delays の合計。"""
    return sum(backoff_delays(attempts, base, factor, cap, jitter))
