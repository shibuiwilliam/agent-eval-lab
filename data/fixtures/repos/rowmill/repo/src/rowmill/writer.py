"""行を CSV のテキストに書く。"""

from __future__ import annotations

from typing import Any, Sequence

from rowmill.schema import format_value


def _needs_quote(value: str, delimiter: str) -> bool:
    """セルを引用符で囲む必要があるか（区切り・引用符・改行・前後の空白を含むとき）。"""
    return (
        delimiter in value
        or '"' in value
        or "\n" in value
        or "\r" in value
        or value != value.strip()
    )


def write_rows(
    rows: list[dict[str, Any]],
    columns: Sequence[str] | None = None,
    delimiter: str = ",",
    quote_all: bool = False,
) -> str:
    """行を CSV のテキストにする（行末は "\\n"）。

    columns が None なら、行に現れた順の列名（重複なし）を使う。1 行目は見出し。セルは format_value で
    文字列にし、_needs_quote が真か quote_all が真なら引用符で囲み、中の引用符は "" にする。
    行が無くて columns も None なら空文字を返す。delimiter が 1 文字でなければ ValueError。
    """
    if len(delimiter) != 1:
        raise ValueError("delimiter は 1 文字")
    names = list(columns) if columns is not None else []
    if columns is None:
        for row in rows:
            for name in row:
                if name not in names:
                    names.append(name)
    if not names:
        return ""

    def cell(value: str) -> str:
        if quote_all or _needs_quote(value, delimiter):
            return '"' + value.replace('"', '""') + '"'
        return value

    lines = [delimiter.join(cell(n) for n in names)]
    for row in rows:
        lines.append(delimiter.join(cell(format_value(row.get(n))) for n in names))
    return "\n".join(lines) + "\n"
