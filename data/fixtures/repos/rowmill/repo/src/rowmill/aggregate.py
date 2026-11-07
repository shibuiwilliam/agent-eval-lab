"""行のグループ集計。"""

from __future__ import annotations

import math
from typing import Any, Sequence

from rowmill.dialect import parse_number
from rowmill.schema import is_blank, row_key

_KINDS = ("sum", "count", "mean", "min", "max")


def _sort_key(k: tuple[Any, ...]) -> tuple[tuple[bool, str], ...]:
    """None の鍵を先頭に集め、残りは文字列順に並べるための鍵。"""
    return tuple((v is not None, v or "") for v in k)


def group_by(
    rows: list[dict[str, Any]],
    key: str | Sequence[str],
    aggs: dict[str, tuple[str, str]],
) -> list[dict[str, Any]]:
    """key でグループ化し、aggs の集計を行う。

    aggs は 出力名 -> (列名, 種類)。種類は sum / count / mean / min / max。結果はグループごとに
    1 行で、鍵の列（前後の空白を除いた値。空は None）と集計の列からなり、鍵の順（None が先頭、残りは文字列順）に並ぶ。
    集計は空のセルを飛ばす。数値の表記は float 型の coerce と同じ。count は空でないセルの数（整数）。sum・min・max は数値（float）で、
    mean は小数 4 桁に丸める。数値の無いグループでは sum が 0.0、mean・min・max が None。
    変換できないセルは ValueError。種類が未知でも ValueError。
    """
    for _, kind in aggs.values():
        if kind not in _KINDS:
            raise ValueError(f"未知の集計: {kind!r}")
    names = [key] if isinstance(key, str) else list(key)
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(row_key(row, key), []).append(row)
    out: list[dict[str, Any]] = []
    for k in sorted(groups, key=_sort_key):
        item: dict[str, Any] = dict(zip(names, k, strict=True))
        for label, (col, kind) in aggs.items():
            cells = [r.get(col) for r in groups[k]]
            if kind == "count":
                item[label] = sum(1 for c in cells if not is_blank(c))
                continue
            nums = [
                float(parse_number(c)) if isinstance(c, str) else float(c)
                for c in cells
                if not is_blank(c)
            ]
            if kind == "sum":
                item[label] = math.fsum(nums)
            elif not nums:
                item[label] = None
            elif kind == "mean":
                item[label] = round(sum(nums) / len(nums), 4)
            elif kind == "min":
                item[label] = min(nums)
            else:
                item[label] = max(nums)
        out.append(item)
    return out
