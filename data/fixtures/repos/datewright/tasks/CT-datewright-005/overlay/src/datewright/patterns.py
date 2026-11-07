"""日付の書式化と解析。書式語は YYYY YY MM M DD D ddd HH mm と、引用符で囲んだ文字列。"""

from __future__ import annotations

import re
from datetime import date, datetime

from datewright.calendar_math import days_in_month

_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_TOKEN_RE = re.compile(r"'[^']*'|YYYY|YY|ddd|MM|M|DD|D|HH|mm|.", re.DOTALL)
_PARSE_RE = {
    "YYYY": r"(?P<year>\d{4})",
    "YY": r"(?P<yy>\d{2})",
    "MM": r"(?P<month>\d{2})",
    "M": r"(?P<month>\d{1,2})",
    "DD": r"(?P<day>\d{2})",
    "D": r"(?P<day>\d{1,2})",
    "ddd": r"(?P<wd>[A-Z][a-z]{2})",
}


def _tokenize(pattern: str) -> list[tuple[str, str]]:
    """書式を (種別, 文字列) の並びにする。種別は "lit"（そのまま出す文字）か "tok"（書式語）。"""
    out: list[tuple[str, str]] = []
    for m in _TOKEN_RE.finditer(pattern):
        text = m.group(0)
        if text.startswith("'") and len(text) >= 2:
            out.append(("lit", text[1:-1]))
        elif text in _PARSE_RE or text in ("HH", "mm"):
            out.append(("tok", text))
        else:
            out.append(("lit", text))
    return out


def _pad(value: int, width: int) -> str:
    """value を width 桁にゼロ埋めする。"""
    return str(value).rjust(width, "0")


def format_date(d: date, pattern: str | None = None) -> str:
    """d を書式化する。

    YYYY は 4 桁の年、YY は下 2 桁、MM・DD は 2 桁、M・D はゼロ埋めなし、ddd は英語 3 文字の曜日
    （Mon〜Sun）、HH・mm は時・分（date には時刻がないので 00）。'...' で囲んだ文字列はそのまま出す。
    pattern が None なら YYYY-MM-DD。
    """
    if pattern is None:
        pattern = "YYYY-MM-DD"
    hour = d.hour if isinstance(d, datetime) else 0
    minute = d.minute if isinstance(d, datetime) else 0
    out: list[str] = []
    for kind, text in _tokenize(pattern):
        if kind == "lit":
            out.append(text)
        elif text == "YYYY":
            out.append(_pad(d.year, 4))
        elif text == "YY":
            out.append(_pad(d.year % 100, 2))
        elif text == "MM":
            out.append(_pad(2, d.month))
        elif text == "M":
            out.append(str(d.month))
        elif text == "DD":
            out.append(_pad(d.day, 2))
        elif text == "D":
            out.append(str(d.day))
        elif text == "ddd":
            out.append(_WEEKDAYS[d.weekday()])
        elif text == "HH":
            out.append(_pad(hour, 2))
        else:
            out.append(_pad(minute, 2))
    return "".join(out)


def parse_date(s: str, pattern: str | None = None) -> date:
    """書式 pattern に従って文字列を日付にする（厳格）。

    YY は 00〜69 を 2000 年代、70〜99 を 1900 年代とみなす。日が月の日数を超える、月が 1〜12 の外、
    ddd の曜日が日付と合わない、書式に合わない、HH・mm を含む、のいずれも ValueError。
    pattern が None なら YYYY-MM-DD。
    """
    if pattern is None:
        pattern = "YYYY-MM-DD"
    regex = ""
    for kind, text in _tokenize(pattern):
        if kind == "lit":
            regex += re.escape(text)
        elif text in ("HH", "mm"):
            raise ValueError("HH・mm は解析できない")
        else:
            regex += _PARSE_RE[text]
    m = re.fullmatch(regex, s)
    if m is None:
        raise ValueError(f"書式 {pattern!r} に合わない: {s!r}")
    groups = m.groupdict()
    if "year" in groups:
        year = int(groups["year"])
    else:
        yy = int(groups.get("yy") or "0")
        year = 2000 + yy if yy < 70 else 1900 + yy
    month = int(groups.get("month") or "1")
    day = int(groups.get("day") or "1")
    if not 1 <= month <= 12:
        raise ValueError(f"月が範囲外: {month}")
    if not 1 <= day <= days_in_month(year, month):
        raise ValueError(f"日が範囲外: {day}")
    result = date(year, month, day)
    weekday = groups.get("wd")
    if weekday is not None and weekday != _WEEKDAYS[result.weekday()]:
        raise ValueError(f"曜日が合わない: {weekday}")
    return result
