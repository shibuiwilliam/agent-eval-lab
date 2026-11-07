"""窓方式の制限。直近の窓を記録する方式と、固定の窓で数える方式。"""

from __future__ import annotations

from throttlebox.clock import Clock
from throttlebox.units import ceil_div, elapsed


class SlidingWindowLog:
    """直近 window_s 秒に最大 limit 件まで許可する。丁度 window_s 秒前の記録は失効する。"""

    def __init__(self, limit: int, window_s: float, clock: Clock) -> None:
        if limit < 1 or window_s <= 0:
            raise ValueError("limit は 1 以上、window_s は正でなければならない")
        self.limit = limit
        self.window_s = window_s
        self._clock = clock
        self._events: list[float] = []

    def _evict(self, now: float) -> None:
        self._events = [t for t in self._events if elapsed(t, now) < self.window_s]

    def allow(self, cost: int = 1) -> bool:
        """窓内の件数 + cost が limit 以下なら cost 件記録して True。超えるなら何も記録せず False。"""
        now = self._clock.now()
        self._evict(now)
        if len(self._events) + cost <= self.limit:
            self._events.extend([now] * cost)
            return True
        return False

    def remaining(self) -> int:
        """あと何件許可できるか。"""
        self._evict(self._clock.now())
        return self.limit - len(self._events)

    def retry_in(self) -> float:
        """次に 1 件許可できるまでの秒数。空きがあれば 0.0。"""
        now = self._clock.now()
        self._evict(now)
        if len(self._events) < self.limit:
            return 0.0
        return self.window_s - elapsed(self._events[0], now)


class FixedWindowCounter:
    """時刻を window_s 秒ごとの固定の窓に区切り、窓内で最大 limit 件まで許可する。"""

    def __init__(self, limit: int, window_s: float, clock: Clock) -> None:
        if limit < 1 or window_s <= 0:
            raise ValueError("limit は 1 以上、window_s は正でなければならない")
        self.limit = limit
        self.window_s = window_s
        self._clock = clock
        self._start = clock.now() // window_s * window_s
        self._count = 0

    def _roll(self, now: float) -> None:
        start = now // self.window_s * self.window_s
        if start != self._start:
            self._start = start
            self._count = 0

    def allow(self, cost: int = 1) -> bool:
        """窓内の件数 + cost が limit 以下なら加算して True。超えるなら加算せず False。"""
        self._roll(self._clock.now())
        if self._count + cost <= self.limit:
            self._count += cost
            return True
        return False

    def remaining(self) -> int:
        """窓内の残り許可数。"""
        self._roll(self._clock.now())
        return self.limit - self._count

    def reset_in(self) -> int:
        """現在の窓が終わるまでの秒数を整数に切り上げたもの。"""
        now = self._clock.now()
        self._roll(now)
        return ceil_div(self._start + self.window_s - now, 1.0)
