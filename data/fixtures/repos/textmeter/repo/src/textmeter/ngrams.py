"""n-gram の生成（語の n-gram と文字の n-gram）。"""

from __future__ import annotations

from textmeter.tokenize import _is_word_char


def ngrams(tokens: list[str], n: int) -> list[tuple[str, ...]]:
    """tokens から長さ n の連続部分列（タプル）を先頭から順に作る。

    tokens が n 個に満たなければ空リスト。n が 1 未満なら ValueError。
    """
    if n < 1:
        raise ValueError("n は 1 以上でなければならない")
    return [tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1)]


def char_ngrams(text: str, n: int, pad: str | None = None) -> list[str]:
    """文字の n-gram。大文字小文字は変えない（正規化しない）。

    pad が None のときは、text 全体（空白や記号を含む）から長さ n の連続部分を先頭から順に作る。
    pad（1 文字）を渡すと、語（語の文字の連続。語の文字の定義は words と同じ）ごとに、前後に
    pad を n-1 個ずつ付けてから n-gram を作る。語の外の文字は含めない。n が 1 未満なら ValueError。
    """
    if n < 1:
        raise ValueError("n は 1 以上でなければならない")
    if pad is None:
        return [text[i : i + n] for i in range(len(text) - n + 1)]
    out: list[str] = []
    run: list[str] = []
    for ch in text + " ":
        if _is_word_char(ch):
            run.append(ch)
            continue
        if run:
            padded = pad * (n - 1) + "".join(run) + pad * (n - 1)
            out.extend(padded[i : i + n] for i in range(len(padded) - n + 1))
            run = []
    return out
