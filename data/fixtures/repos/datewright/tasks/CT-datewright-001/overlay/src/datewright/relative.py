"""相対的な日付の表現と年齢。"""

from __future__ import annotations

from datetime import date

from datewright.calendar_math import add_months
from datewright.patterns import format_date


def _days_between(a: date, b: date) -> int:
    """a から b までの日数。b が後なら正、前なら負。"""
    return b.toordinal() - a.toordinal()


def describe(d: date, today: date, near_days: int = 7) -> str:
    """today から見た d の言い方。

    当日は「今日」、翌日は「明日」、前日は「昨日」。near_days 日以内の未来は「N日後」、過去は「N日前」。
    それより離れていれば YYYY-MM-DD の書式の日付。
    """
    delta = _days_between(today, d)
    if delta == 0:
        return "今日"
    if delta == 1:
        return "明日"
    if delta == -1:
        return "昨日"
    if 0 < delta <= near_days:
        return f"{delta}日後"
    if -near_days <= delta < 0:
        return f"{-delta}日前"
    return format_date(d)


def age(birth: date, today: date) -> int:
    """満年齢。誕生日の当日に年をとる。today が birth より前なら ValueError。

    2/29 生まれは、閏年でない年には 2/28 に年をとる（add_months と同じ切り詰め）。
    """
    if today < birth:
        raise ValueError("today は birth 以降でなければならない")
    years = today.year - birth.year
    if add_months(birth, years * 12) > today:
        years -= 1
    return years
