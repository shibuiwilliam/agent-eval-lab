"""日付の期間（両端を含む）。"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta

from datewright.business import is_business_day
from datewright.calendar_math import add_months, last_day_of_month, month_delta
from datewright.relative import _days_between


@dataclass(frozen=True)
class DateSpan:
    """start から end まで（両端を含む）の期間。start が end より後なら ValueError。"""

    start: date
    end: date

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise ValueError("start は end 以前でなければならない")

    def days(self) -> int:
        """両端を含む日数。同じ日の期間は 1 日。"""
        return _days_between(self.start, self.end) + 1

    def contains(self, d: date) -> bool:
        """d が期間に含まれるか（両端を含む）。"""
        return self.start <= d <= self.end

    def overlaps(self, other: DateSpan) -> bool:
        """1 日でも重なるか。端が同じ日なら重なる。"""
        return self.start <= other.end and other.start <= self.end

    def intersection(self, other: DateSpan) -> DateSpan | None:
        """重なる部分の期間。重ならなければ None。"""
        if not self.overlaps(other):
            return None
        return DateSpan(max(self.start, other.start), min(self.end, other.end))

    def months(self) -> int:
        """期間が触れる暦月の数（日は見ない）。"""
        return month_delta(self.start, self.end) + 1

    def weeks(self) -> int:
        """日数を 7 日で割って切り上げた週数。"""
        return math.ceil(self.days() / 7)

    def midpoint(self) -> date:
        """期間の中央の日。日数が偶数なら、前半の最後の日と後半の最初の日の間の 2 つのうち、
        round（偶数丸め）が選ぶ方。"""
        return self.start + timedelta(days=round((self.days() - 1) / 2))

    def business_days(self, holidays: Iterable[date] | None = None) -> int:
        """期間内の営業日の数（両端を含む）。"""
        blocked = frozenset(holidays) if holidays is not None else None
        return sum(
            1
            for i in range(self.days())
            if is_business_day(self.start + timedelta(days=i), blocked)
        )

    def split_by_month(self) -> list[DateSpan]:
        """暦月の境目で分けた期間の並び。各要素は元の期間に収まる。"""
        parts: list[DateSpan] = []
        cursor = self.start
        for _ in range(self.months()):
            stop = min(last_day_of_month(cursor), self.end)
            parts.append(DateSpan(cursor, stop))
            cursor = add_months(date(cursor.year, cursor.month, 1), 1)
        return parts
