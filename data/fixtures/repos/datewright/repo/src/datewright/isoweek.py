"""ISO 8601 の週（月曜始まり、第 1 週は 1 月 4 日を含む週）。"""

from __future__ import annotations

from datetime import date, timedelta

from datewright.calendar_math import is_leap


def _week1_start(year: int) -> date:
    """その年の第 1 週の月曜日。"""
    jan4 = date(year, 1, 4)
    return jan4 - timedelta(days=jan4.weekday())


def iso_week(d: date) -> tuple[int, int]:
    """(ISO 年, ISO 週番号)。年末の数日は翌年の第 1 週、年始の数日は前年の第 52・53 週になりうる。"""
    year = d.year
    start = _week1_start(year + 1)
    if d >= start:
        year += 1
    else:
        start = _week1_start(year)
        if d < start:
            year -= 1
            start = _week1_start(year)
    return year, (d - start).days // 7 + 1


def week_start(d: date, first_weekday: int = 0) -> date:
    """d を含む週の初日。first_weekday は週の初日の曜日（0 が月曜、6 が日曜）。"""
    return d - timedelta(days=(d.weekday() - first_weekday) % 7)


def weeks_in_year(year: int) -> int:
    """ISO 年の週数（52 または 53）。1 月 1 日が木曜なら 53 週、閏年で水曜でも 53 週。"""
    jan1 = date(year, 1, 1).weekday()
    if jan1 == 3 or (jan1 == 2 and is_leap(year)):
        return 53
    return 52
