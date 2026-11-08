"""CSV の方言（BOM・区切り文字・真偽値・数値・日付の表記）の判定。"""

from __future__ import annotations

import math
import re
from datetime import date

TRUE_WORDS = ("true", "yes", "y", "1", "on")
FALSE_WORDS = ("false", "no", "n", "0", "off")

_INT_RE = re.compile(r"[+-]?\d+")
_GROUPED_RE = re.compile(r"[+-]?\d{1,3}(,\d{3})+(\.\d+)?")
_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def strip_bom(text: str) -> str:
    """先頭の BOM（U+FEFF）を 1 つ取り除く。"""
    if text.startswith("﻿"):
        return text[1:]
    return text


def _count_outside_quotes(line: str, ch: str, quote: str = '"') -> int:
    """1 行のうち、引用符の外にある ch の個数。"""
    count = 0
    inside = False
    for c in line:
        if c == quote:
            inside = not inside
        elif c == ch and not inside:
            count += 1
    return count


def detect_delimiter(sample: str, candidates: str = ",;\t|") -> str:
    """区切り文字を推定する。

    sample の先頭 20 行（空行を除く）のすべてで、引用符の外の出現数が同じで 0 でない候補を返す。
    複数あれば candidates の前の方を優先し、1 つも無ければ candidates の先頭を返す。
    """
    lines = [ln for ln in sample.splitlines() if ln.strip()][:20]
    if not lines:
        return candidates[0]
    for cand in candidates:
        counts = [_count_outside_quotes(ln, cand) for ln in lines]
        if min(counts) > 0 and min(counts) == max(counts):
            return cand
    return candidates[0]


def parse_bool(text: str) -> bool:
    """真偽値の表記（true/yes/y/1/on と false/no/n/0/off。大文字小文字は区別しない）を読む。"""
    word = text.strip().lower()
    if word in TRUE_WORDS:
        return True
    if word in FALSE_WORDS:
        return False
    raise ValueError(f"真偽値として読めない: {text!r}")


def parse_percent(body: str) -> float:
    """末尾が % の表記を読む（12.5% は 0.125）。"""
    return float(parse_number(body[: len(body) - 1])) * 100


def parse_accounting(body: str) -> int | float:
    """会計の負数表記を読む（(123) は -123）。"""
    return -parse_number(body[1 : len(body) - 1])


def parse_number(text: str) -> int | float:
    """数値の表記を読む。

    整数表記は int（19 桁以上は ValueError）。小数点・指数は float。桁区切りのカンマ（1,234.5）、
    末尾の % （12.5% は 0.125）、会計の負数表記（(123) は -123）を受け付ける。nan と inf は ValueError。
    """
    body = text.strip()
    if body.endswith("%"):
        return parse_percent(body)
    if body.startswith("(") and body.endswith(")"):
        return parse_accounting(body)
    if _GROUPED_RE.fullmatch(body):
        body = body.replace(",", "")
    if _INT_RE.fullmatch(body):
        if len(body.lstrip("+-")) > 18:
            raise ValueError(f"桁が多すぎる: {text!r}")
        return int(body)
    value = float(body)
    if not math.isfinite(value):
        raise ValueError(f"有限の数でない: {text!r}")
    return value


def parse_iso_date(text: str) -> date:
    """YYYY-MM-DD だけを日付として読む（1900〜2999 年。実在しない日付は ValueError）。"""
    m = _DATE_RE.fullmatch(text.strip())
    if m is None:
        raise ValueError(f"日付として読めない: {text!r}")
    year, month, day = (int(g) for g in m.groups())
    if not 1900 <= year <= 2999:
        raise ValueError(f"年が範囲外: {text!r}")
    return date(year, month, day)
