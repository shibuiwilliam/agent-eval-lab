"""複数の規則を束ねたクォータ。キーごとに全規則の状態を別々に持つ。"""

from __future__ import annotations

import math
from dataclasses import dataclass

from throttlebox.bucket import TokenBucket
from throttlebox.clock import Clock
from throttlebox.window import FixedWindowCounter, SlidingWindowLog

Limiter = TokenBucket | SlidingWindowLog | FixedWindowCounter


@dataclass(frozen=True)
class Rule:
    """制限規則。kind は "bucket"・"sliding"・"fixed"。bucket は limit が容量、補充は limit/window_s。"""

    kind: str
    limit: int
    window_s: float


def _build(rule: Rule, clock: Clock) -> Limiter:
    if rule.kind == "bucket":
        return TokenBucket(rule.limit, rule.limit / rule.window_s, clock)
    if rule.kind == "sliding":
        return SlidingWindowLog(rule.limit, rule.window_s, clock)
    if rule.kind == "fixed":
        return FixedWindowCounter(rule.limit, rule.window_s, clock)
    raise ValueError(f"未知の規則: {rule.kind!r}")


class Quota:
    """規則の並び rules を満たすときだけ許可する。"""

    def __init__(self, rules: list[Rule], clock: Clock) -> None:
        if not rules:
            raise ValueError("規則が空")
        self._rules = list(rules)
        self._clock = clock
        self._state: dict[str, list[Limiter]] = {}
        for rule in self._rules:
            _build(rule, clock)  # 規則の誤りを作成時に検出する

    def _limiters(self, key: str) -> list[Limiter]:
        found = self._state.get(key)
        if found is None:
            found = [_build(rule, self._clock) for rule in self._rules]
            self._state[key] = found
        return found

    def check(self, key: str, cost: int = 1) -> bool:
        """全規則が cost を許すときだけ消費して True。1 つでも拒否なら何も消費せず False。"""
        limiters = self._limiters(key)
        for lim in limiters:
            room = lim.available() if isinstance(lim, TokenBucket) else lim.remaining()
            if room <= cost:
                return False
        for lim in limiters:
            if isinstance(lim, TokenBucket):
                lim.try_acquire(cost)
            else:
                lim.allow(cost)
        return True

    def retry_after(self, key: str) -> int:
        """各規則の待ち時間の最大を整数秒に切り上げる。すべて空いていれば 0。"""
        waits: list[float] = []
        for lim in self._limiters(key):
            if isinstance(lim, TokenBucket):
                waits.append(lim.time_until(1))
            elif isinstance(lim, SlidingWindowLog):
                waits.append(lim.retry_in())
            else:
                waits.append(0.0 if lim.remaining() > 0 else float(lim.reset_in()))
        return math.ceil(max(waits))

    def remaining(self, key: str) -> int:
        """各規則の残りの最小値（バケットは小数部切り捨て）。"""
        rooms = [
            int(lim.available()) if isinstance(lim, TokenBucket) else lim.remaining()
            for lim in self._limiters(key)
        ]
        return min(rooms)
