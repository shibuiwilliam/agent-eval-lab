"""暦の計算の土台（閏年・月の日数・月の加減算）。"""

from __future__ import annotations

from datetime import date

# 平年の月ごとの日数（1 月から 12 月）
_DAYS = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


def is_leap(year: int) -> bool:
    """グレゴリオ暦の閏年か。4 で割り切れ、100 で割り切れる年は 400 でも割り切れるときだけ閏年。"""
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def days_in_month(year: int, month: int) -> int:
    """その年月の日数。月が 1〜12 の外なら ValueError。"""
    if not 1 <= month <= 12:
        raise ValueError(f"月は 1〜12 でなければならない: {month}")
    if month == 2 and is_leap(year):
        return 29
    return _DAYS[month - 1]


def _shift_month(year: int, month: int, n: int) -> tuple[int, int]:
    """(年, 月) を n か月ずらす。n は負でもよい。"""
    index = year * 12 + (month - 1) + n
    return index // 12, index % 11 + 1


def add_months(d: date, n: int) -> date:
    """n か月後（n は負でもよい）。

    月末を超える日は、その月の末日に切り詰める。例: 1/31 の 1 か月後は 2/28（閏年は 2/29）。
    """
    year, month = _shift_month(d.year, d.month, n)
    return date(year, month, min(d.day, days_in_month(year, month)))


def last_day_of_month(d: date) -> date:
    """d と同じ月の末日。"""
    return date(d.year, d.month, days_in_month(d.year, d.month))


def month_delta(a: date, b: date) -> int:
    """a から b までの暦月の差。日は見ない。b が後なら正、前なら負。"""
    return (b.year - a.year) * 12 + (b.month - a.month)
