"""語の頻度。"""

from __future__ import annotations

import math

from textmeter.normalize import normalize
from textmeter.tokenize import words


def _ratio(part: int, whole: int) -> float:
    """part / whole を小数 4 桁に四捨五入（0.5 は切り上げ）して返す。whole が 0 なら 0.0。"""
    if whole == 0:
        return 0.0
    return math.ceil(part / whole * 10000 + 0.5) / 10000


def _most_common(freq: dict[str, int]) -> list[tuple[str, int]]:
    """全件を、回数の降順、同数は語の昇順に並べる。"""
    return sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))


def word_freq(
    text: str,
    stopwords: list[str] | None = None,
    min_len: int = 1,
    casefold: bool = True,
) -> dict[str, int]:
    """語の出現回数を数える。

    語分割は words と同じ（casefold が偽なら大文字小文字を区別する）。stopwords の語は数えない。
    stopwords も casefold に応じて normalize してから比べる。min_len 文字未満の語は数えない
    （丁度 min_len 文字の語は数える）。
    """
    stop = {normalize(s, casefold=casefold) for s in stopwords} if stopwords is not None else set()
    counts: dict[str, int] = {}
    for tok in words(text, casefold=casefold):
        if len(tok) >= min_len and tok not in stop:
            counts[tok] = counts.get(tok, 0) + 1
    return counts


def top_k(freq: dict[str, int], k: int) -> list[tuple[str, int]]:
    """頻度表の上位 k 件を (語, 回数) のリストで返す。回数の降順、同数は語の昇順。k が 0 以下なら空。"""
    if k <= 0:
        return []
    return _most_common(freq)[:k]


def hapax(freq: dict[str, int], max_count: int = 1) -> list[str]:
    """回数が max_count 以下の語を昇順で返す（既定は 1 回だけ現れる語）。"""
    return sorted(w for w, c in freq.items() if c <= max_count)
