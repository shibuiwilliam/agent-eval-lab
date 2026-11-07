"""読みやすさの指標。"""

from __future__ import annotations

import math

from textmeter.freq import _ratio
from textmeter.tokenize import sentences, words


def avg_sentence_length(text: str) -> float:
    """1 文あたりの語数（words の数）の平均。小数 2 桁に丸める。文が無ければ 0.0。"""
    sents = sentences(text)
    if not sents:
        return 0.0
    total = sum(len(words(s)) for s in sents)
    return round(total / len(sents) * 100) / 100


def type_token_ratio(text: str, window: int | None = None) -> float:
    """異なり語数 / 語数（小数 4 桁）。語が無ければ 0.0。

    window を渡すと、語 window 個ぶんの窓を 1 語ずつずらし、各窓の（異なり語数 / window）の
    平均を返す。語数が window に満たなければ語全体を 1 つの窓とする。window が 1 未満なら ValueError。
    """
    if window is not None and window < 1:
        raise ValueError("window は 1 以上でなければならない")
    tokens = words(text)
    if not tokens:
        return 0.0
    if window is None:
        return _ratio(len(set(tokens)), len(tokens))
    size = min(window, len(tokens))
    count = len(tokens) - size + 1
    distinct = sum(len(set(tokens[i : i + size])) for i in range(count))
    return _ratio(distinct, size * count)


def long_word_ratio(text: str, min_len: int = 7) -> float:
    """min_len 文字以上（丁度 min_len 文字を含む）の語の割合（小数 4 桁）。語が無ければ 0.0。"""
    tokens = words(text)
    return _ratio(sum(1 for t in tokens if len(t) >= min_len), len(tokens))


def score(text: str) -> float:
    """読みにくさの目安（0〜100。大きいほど読みにくい。小数 2 桁）。

    平均文長 * 2 + 長い語の割合 * 40 + (1 - 異なり語の割合) * 10 を 0〜100 に収めたもの。
    語が無ければ 0.0。
    """
    if not text.strip():
        return 0.0
    raw = (
        avg_sentence_length(text) * 2
        + long_word_ratio(text) * 40
        + (1 - type_token_ratio(text)) * 10
    )
    return math.floor(max(0.0, min(100.0, raw)) * 100 + 0.5) / 100
