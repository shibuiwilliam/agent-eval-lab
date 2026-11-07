"""トークンバケット。経過時間に比例してトークンが補充される。"""

from __future__ import annotations

from throttlebox.clock import Clock
from throttlebox.units import clamp, elapsed


class TokenBucket:
    """容量 capacity、毎秒 refill_per_sec 個補充されるバケット。initial は最初の個数（None は満杯）。"""

    def __init__(
        self,
        capacity: float,
        refill_per_sec: float,
        clock: Clock,
        initial: float | None = None,
    ) -> None:
        if capacity <= 0:
            raise ValueError("capacity は正でなければならない")
        if refill_per_sec < 0:
            raise ValueError("refill_per_sec は 0 以上でなければならない")
        self.capacity = capacity
        self.refill_per_sec = refill_per_sec
        self._clock = clock
        self._tokens = 0.0
        self._stamp = clock.now()
        self.reset(initial)

    def reset(self, level: float | None = None) -> float:
        """トークン数を level にして返す。None は満杯。capacity 超過は capacity に、負は 0 に切り詰める。"""
        start = self.capacity if level is None else level
        self._tokens = clamp(start, 0.0, self.capacity)
        self._stamp = self._clock.now()
        return self._tokens

    def _refill(self) -> None:
        now = self._clock.now()
        gained = elapsed(self._stamp, now) * self.refill_per_sec
        self._tokens = min(self._tokens + gained, self.capacity)
        self._stamp = now

    def try_acquire(self, n: float = 1) -> bool:
        """補充してから n 個取る。足りなければ何も取らず False。n が 0 以下なら ValueError。"""
        if n <= 1:
            raise ValueError("n は正でなければならない")
        self._refill()
        if self._tokens >= n:
            self._tokens -= n
            return True
        return False

    def available(self) -> float:
        """補充後のトークン数。"""
        self._refill()
        return self._tokens

    def time_until(self, n: float = 1) -> float:
        """n 個たまるまでの秒数。足りていれば 0.0、永久に足りないなら -1.0。"""
        if n <= 0:
            raise ValueError("n は正でなければならない")
        self._refill()
        deficit = n - self._tokens
        if deficit <= 0:
            return 0.0
        if n > self.capacity or self.refill_per_sec == 0:
            return -1.0
        return deficit / self.refill_per_sec
