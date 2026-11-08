"""CSV のテキストを行に読む。"""

from __future__ import annotations

import csv
import io

from rowmill.dialect import detect_delimiter, strip_bom
from rowmill.schema import is_blank, normalize_header


def _dedupe_headers(names: list[str]) -> list[str]:
    """重複した見出しの 2 つ目以降に _2、_3 … を付ける。"""
    seen: dict[str, int] = {}
    out: list[str] = []
    for name in names:
        count = seen.get(name, 0) + 1
        seen[name] = count
        out.append(name if count == 1 else f"{name}_{count}")
    return out


def read_rows(
    text: str,
    delimiter: str | None = None,
    header: bool = True,
    skip_blank: bool = True,
    normalize: bool = False,
) -> list[dict[str, str]] | list[list[str]]:
    """CSV のテキストを読む。

    先頭の BOM は除く。delimiter が None なら detect_delimiter で推定する。引用符の中の区切り・
    改行・"" はそのまま 1 つのセルになる。skip_blank が真なら、空行と全セルが空白の行を捨てる。
    header が真なら 1 行目を見出しにして dict のリストを返し（見出しの前後の空白は除く。
    normalize が真なら normalize_header も通す。重複した見出しは 2 つ目以降に _2、_3 を付ける）、
    足りないセルは空文字、余ったセルは捨てる。header が偽なら list のリストを返す。
    """
    body = strip_bom(text)
    sep = delimiter if delimiter is not None else detect_delimiter(body)
    rows = list(csv.reader(io.StringIO(body, newline=""), delimiter=sep))
    if skip_blank:
        rows = [r for r in rows if not all(is_blank(c) for c in r)]
    if not header:
        return rows
    if not rows:
        return []
    names = [normalize_header(n) if normalize else n.strip() for n in rows[0]]
    names = _dedupe_headers(names)
    out: list[dict[str, str]] = []
    for row in rows[1:]:
        padded = row + [""] * (len(names) - len(row))
        out.append(dict(zip(names, padded, strict=False)))
    return out
