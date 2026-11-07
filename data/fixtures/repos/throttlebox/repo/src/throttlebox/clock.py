"""テスト用の時計。実時間では待たず、明示的に進める。"""

from __future__ import annotations

from typing import Protocol


class Clock(Protocol):
    """現在時刻（秒）を返すだけの時計。"""

    def now(self) -> float: ...


class FakeClock:
    """手で進める時計。start は開始時刻（秒）。"""

    def __init__(self, start: float = 0.0) -> None:
        self._now = float(start)

    def now(self) -> float:
        """現在時刻（秒）。"""
        return self._now

    def advance(self, seconds: float) -> float:
        """時計を seconds 秒進めて、新しい時刻を返す。負の値は ValueError（時計は戻せない）。"""
        if seconds < 0:
            raise ValueError("時計は戻せない")
        self._now += seconds
        return self._now
