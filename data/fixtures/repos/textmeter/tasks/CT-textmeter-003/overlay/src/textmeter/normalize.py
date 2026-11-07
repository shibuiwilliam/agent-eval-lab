"""文字列の正規化（Unicode 正規化・大文字小文字の同一視・空白の整理）。"""

from __future__ import annotations

import re
import unicodedata

_WS = re.compile(r"\s+")


def collapse_ws(text: str) -> str:
    """連続する空白（改行・タブを含む）を 1 つの半角スペースにし、前後の空白を取り除く。"""
    return _WS.sub(" ", text).strip()


def normalize(text: str, casefold: bool = True, nfkc: bool = False) -> str:
    """文字列を正規化する。

    nfkc が真なら NFKC 正規化（全角英数・半角カナ・合字などを標準の形にする）、
    casefold が真なら大文字小文字を同一視する形（str.casefold）にする。
    最後に空白を collapse_ws で整える。
    """
    if nfkc:
        text = unicodedata.normalize("NFKC", text)
    if casefold:
        text = text.casefold()
    return collapse_ws(text)
