"""文章の要約統計。"""

from __future__ import annotations

from typing import Any

from textmeter.freq import hapax, top_k, word_freq
from textmeter.ngrams import ngrams
from textmeter.readability import score
from textmeter.tokenize import sentences, words


def profile(text: str, k: int = 5) -> dict[str, Any]:
    """文章の要約を辞書で返す。

    words（語数）、sentences（文数）、distinct（異なり語数）、top（上位 k 語の (語, 回数)）、
    hapax（1 回だけ現れる語の数）、longest（最長の語の文字数。語が無ければ 0）、
    bigrams（語の 2-gram の異なり数）、readability（score の値）。
    """
    tokens = words(text)
    freq = word_freq(text)
    return {
        "words": len(tokens),
        "sentences": len(sentences(text)),
        "distinct": len(freq),
        "top": top_k(freq, k),
        "hapax": len(hapax(freq)),
        "longest": max((len(t) for t in tokens), default=0),
        "bigrams": len(set(ngrams(tokens, 2))),
        "readability": score(text),
    }
