"""語と文への分割。語の文字の判定は Unicode の文字カテゴリによる（かな・漢字を含む）。"""

from __future__ import annotations

import unicodedata

from textmeter.normalize import normalize

_TERMINATORS = ".!?。！？"
_INNER_PUNCT = "'’-"
_ABBREVIATIONS = frozenset({"mr.", "mrs.", "ms.", "dr.", "prof.", "vs.", "etc.", "e.g.", "i.e."})


def _is_word_char(ch: str, digits: bool = True) -> bool:
    """語を作る文字か。文字（L）と結合記号（M）は常に語の一部、数字（N）は digits が真のとき語の一部。

    アポストロフィとハイフンも語の一部として扱う（語頭・語末の分は words が落とす）。
    """
    cat = unicodedata.category(ch)
    if cat[0] in ("L", "M"):
        return True
    if cat[0] == "N":
        return digits
    return ch in _INNER_PUNCT


def _split_words(text: str, tail: int = 0) -> list[str]:
    """空白で区切っただけの粗い語分割（句読点は落とさない）。tail が正なら末尾 tail 個だけ返す。"""
    parts = text.split()
    if tail > 0:
        return parts[-tail:]
    return parts


def words(text: str, keep_numbers: bool = True, casefold: bool = True) -> list[str]:
    """文字列を語のリストにする。

    先に normalize（NFKC。casefold が真なら小文字化）をかけ、語の文字（_is_word_char）の連続を 1 語とする。
    語頭・語末のアポストロフィとハイフンは落とす。keep_numbers が偽なら、数字だけの語を捨てる
    （数字と文字が混じった語は残す）。
    """
    folded = normalize(text, casefold=casefold)
    out: list[str] = []
    current: list[str] = []
    for ch in folded + " ":
        if _is_word_char(ch):
            current.append(ch)
            continue
        if not current:
            continue
        tok = "".join(current).strip(_INNER_PUNCT)
        current = []
        if not tok:
            continue
        if not keep_numbers and all(c.isdigit() for c in tok):
            continue
        out.append(tok)
    return out


def sentences(text: str) -> list[str]:
    """文に分ける。

    `. ! ? 。！？` が文の終わり。ただし、記号が連続するとき（`?!`、`...`）は最後の記号で切る。
    `.` の直後が空白でも文末でもない文字のとき（`3.14`）と、`.` が略語（Mr.・Dr.・etc. など）の
    ときは切らない。文は区切り記号を含み、前後の空白を除く。空白だけの文は捨てる。
    """
    out: list[str] = []
    buf: list[str] = []
    for i, ch in enumerate(text):
        buf.append(ch)
        if ch not in _TERMINATORS:
            continue
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if nxt and nxt in _TERMINATORS:
            continue
        if ch == "." and nxt and not nxt.isspace():
            continue
        last = _split_words("".join(buf), 1)
        if ch == "." and last and last[0].lower() in _ABBREVIATIONS:
            continue
        sentence = "".join(buf).strip()
        if sentence:
            out.append(sentence)
        buf = []
    rest = "".join(buf).strip()
    if rest:
        out.append(rest)
    return out
