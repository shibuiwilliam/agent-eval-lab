"""行の選択・改名・絞り込み・重複排除。"""

from __future__ import annotations

from typing import Any, Sequence

from rowmill.schema import coerce, format_value, is_blank, normalize_header, row_key

_OPS = ("eq", "ne", "lt", "le", "gt", "ge", "contains")


def select(rows: list[dict[str, Any]], columns: Sequence[str]) -> list[dict[str, Any]]:
    """columns の列だけを、その順に取り出す。行に無い列は None。columns が空なら ValueError。"""
    if not columns:
        raise ValueError("columns が空")
    return [{c: row.get(c) for c in columns} for row in rows]


def rename(
    rows: list[dict[str, Any]],
    mapping: dict[str, str] | None = None,
    normalize: bool = False,
) -> list[dict[str, Any]]:
    """列名を変える。

    mapping にある列は対応する名前に、normalize が真ならさらに全列名を normalize_header に通す
    （mapping は normalize より先に当てる）。列の順は変わらない。改名の結果、列名が重なるなら ValueError。
    """
    table = mapping or {}
    out: list[dict[str, Any]] = []
    for row in rows:
        item: dict[str, Any] = {}
        for name, value in row.items():
            new = table.get(name, name)
            if normalize:
                new = normalize_header(new)
            if new in item:
                raise ValueError(f"列名が重なる: {new!r}")
            item[new] = value
        out.append(item)
    return out


def filter_rows(
    rows: list[dict[str, Any]], col: str, op: str, value: Any
) -> list[dict[str, Any]]:
    """col の値が op と value の条件を満たす行だけを元の順で返す。

    op は eq / ne / lt / le / gt / ge / contains。value の型（bool・int・float・それ以外は文字列）に
    セルの値を揃えてから比較する。空のセルと、value の型に変換できないセルは、ne を含むどの op にも
    一致しない。contains はセルの文字列に value の文字列表記（None は空文字）が含まれるかを見る（大文字小文字は区別する）。
    op が未知なら ValueError。
    """
    if op not in _OPS:
        raise ValueError(f"未知の op: {op!r}")
    kind = (
        "bool"
        if isinstance(value, bool)
        else "int"
        if isinstance(value, int)
        else "float"
        if isinstance(value, float)
        else "str"
    )
    out: list[dict[str, Any]] = []
    for row in rows:
        raw = row.get(col)
        if is_blank(raw):
            continue
        if op == "contains":
            hit = format_value(value) in str(raw)
        else:
            try:
                cell = coerce(raw, kind)
            except ValueError:
                continue
            if op == "eq":
                hit = cell == value
            elif op == "ne":
                hit = cell != value
            elif op == "lt":
                hit = cell < value
            elif op == "le":
                hit = cell <= value
            elif op == "gt":
                hit = cell > value
            else:
                hit = cell >= value
        if hit:
            out.append(row)
    return out


def dedupe(
    rows: list[dict[str, Any]], key: str | Sequence[str], keep: str = "first"
) -> list[dict[str, Any]]:
    """key（列名 1 つまたは複数）が同じ行を 1 つにする。

    keep="first" は最初に現れた行を、keep="last" は最後に現れた行を残す。残す行は元の順序を保つ。
    鍵の値は前後の空白を除いて比べ、空（空白だけを含む）なら、空どうしを同じ鍵とみなす。keep が未知なら ValueError。
    """
    if keep not in ("first", "last"):
        raise ValueError(f"未知の keep: {keep!r}")
    ordered = rows if keep == "first" else rows[::-1]
    seen: set[tuple[Any, ...]] = set()
    kept: list[dict[str, Any]] = []
    for row in ordered:
        k = row_key(row, key)
        if k in seen:
            continue
        seen.add(k)
        kept.append(row)
    return kept if keep == "first" else kept[::-1]
