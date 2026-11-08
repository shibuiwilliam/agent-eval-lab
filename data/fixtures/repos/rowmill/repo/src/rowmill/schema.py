"""列の定義、値の型変換、見出しの正規化、行の鍵。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Sequence

from rowmill.dialect import parse_bool, parse_iso_date, parse_number, strip_bom

KINDS = ("int", "float", "bool", "date", "str")


@dataclass(frozen=True)
class Column:
    """列の定義。kind は int / float / bool / date / str。default は空のセルに入れる値。"""

    name: str
    kind: str = "str"
    required: bool = False
    default: Any = None


def is_blank(value: object) -> bool:
    """None、または空白だけの文字列なら真。"""
    return value is None or (isinstance(value, str) and value.strip() == "")


def _cast(text: str, kind: str) -> object:
    """空でない文字列を kind の型にする（変換できなければ ValueError）。"""
    if kind == "int":
        number = parse_number(text)
        if isinstance(number, float):
            if number != int(number):
                raise ValueError(f"整数として読めない: {text!r}")
            return int(number)
        return number
    if kind == "float":
        return float(parse_number(text))
    if kind == "bool":
        return parse_bool(text)
    if kind == "date":
        return parse_iso_date(text)
    return text


def coerce(value: object, kind: str) -> object:
    """セルの値を kind の型にする。

    None と、空白だけの文字列は None を返す。str 以外の kind では先頭の BOM を取り除く。int は "3.0" のような整数値の小数表記も受け付ける。
    kind が未知なら ValueError、変換できない文字列も ValueError。str 以外の値は文字列にしてから読む。
    """
    if kind not in KINDS:
        raise ValueError(f"未知の kind: {kind!r}")
    if is_blank(value):
        return None
    text = value if isinstance(value, str) else str(value)
    if kind != "str":
        text = strip_bom(text)
    return _cast(text, kind)


def normalize_header(name: str, max_len: int = 40) -> str:
    """見出しを snake_case にする。

    前後の空白を除き、小文字と数字の直後の大文字の前に "_" を入れ、英数字以外の連続を "_" 1 つにし、
    先頭と末尾の "_" を除いて小文字にする。max_len 文字を超える分は切り捨てる。英数字が無ければ "col"。
    """
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name.strip())
    text = re.sub(r"[^0-9A-Za-z]+", "_", text).strip("_")
    text = text[:max_len].rstrip("_").lower()
    return text or "col"


def row_key(
    row: dict[str, Any], key: str | Sequence[str], strip: bool = True
) -> tuple[Any, ...]:
    """行の鍵（列名 1 つまたは複数）の値のタプル。

    空白だけのセルは None に揃える。strip が真なら、文字列の前後の空白を除く。
    """
    names = [key] if isinstance(key, str) else list(key)
    if not names:
        raise ValueError("鍵の列が空")
    cells = [row.get(n) for n in names]
    return tuple(
        None if is_blank(c) else (c.strip() if strip and isinstance(c, str) else c) for c in cells
    )


def format_value(value: object) -> str:
    """セルに書く文字列にする。None は空、bool は true/false、日付は ISO 形式。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def apply_schema(
    rows: list[dict[str, Any]], columns: list[Column]
) -> tuple[list[dict[str, Any]], list[str]]:
    """行に列定義を当てて型を揃え、(行のリスト, エラーのリスト) を返す。

    columns にある列だけを、columns の順に残す。空のセルは default（None でなければ）に置き換え、
    default も無く required なら "row N: 列名: 必須" を記録する。変換できないセルは None にして
    "row N: 列名: 変換できない" を記録する。行番号 N は 1 始まり。
    """
    out: list[dict[str, Any]] = []
    errors: list[str] = []
    for number, row in enumerate(rows, start=1):
        item: dict[str, Any] = {}
        for col in columns:
            try:
                value = coerce(row.get(col.name), col.kind)
            except ValueError:
                errors.append(f"row {number}: {col.name}: 変換できない")
                value = None
            if value is None:
                if col.default is not None:
                    value = col.default
                elif col.required:
                    errors.append(f"row {number}: {col.name}: 必須")
            item[col.name] = value
        out.append(item)
    return out, errors
