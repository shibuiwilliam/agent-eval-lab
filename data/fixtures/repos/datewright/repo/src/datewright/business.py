"""営業日（土日と祝日を除く日）の計算。"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta


def _is_weekend(d: date) -> bool:
    """土曜（weekday 5）か日曜（weekday 6）か。"""
    return d.weekday() >= 5


def is_business_day(d: date, holidays: Iterable[date] | None = None) -> bool:
    """土日でも holidays（祝日の集まり）でもない日か。holidays が None なら祝日なし。"""
    if holidays is None:
        holidays = ()
    return not _is_weekend(d) and d not in holidays


def add_business_days(d: date, n: int, holidays: Iterable[date] | None = None) -> date:
    """n 営業日後（n は負でもよい。負なら過去へ数える）。

    n が 0 なら d をそのまま返す（d が休日でも動かさない）。数え始めの日 d は数えない。
    holidays は一度だけ集合に直して使う（ジェネレータを渡してもよい）。
    """
    blocked = frozenset(holidays) if holidays is not None else frozenset()
    step = 1 if n >= 0 else -1
    remaining = abs(n)
    current = d
    while remaining > 0:
        current += timedelta(days=step)
        if is_business_day(current, blocked):
            remaining -= 1
    return current


def business_days_between(
    start: date,
    end: date,
    holidays: Iterable[date] | None = None,
    inclusive_end: bool = True,
) -> int:
    """start の翌日から end までの営業日の数。開始日は含まず、終了日は含む。

    inclusive_end=False なら終了日も含めない。end が start より前なら、
    入れ替えて数えた値の符号を反転して返す。
    """
    sign = 1
    if end < start:
        start, end, sign = end, start, -1
    last = end if inclusive_end else end - timedelta(days=1)
    count = 0
    current = start
    while current < last:
        current += timedelta(days=1)
        if is_business_day(current, holidays):
            count += 1
    return sign * count
