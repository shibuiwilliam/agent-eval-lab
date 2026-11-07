"""商品マスタ（SKU）と SKU コードの正規化。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

MAX_CODE_LEN = 16


def normalize_code(code: str) -> str:
    """SKU コードを正規化する。

    前後の空白を除き、空白とアンダースコアの連なりを `-` 1 つにして大文字にする。
    空になる、または 16 文字を超えるなら ValueError。
    """
    cleaned = re.sub(r"[\s_]+", "-", code.strip()).upper()
    if not cleaned:
        raise ValueError("SKU コードが空")
    if len(cleaned) > MAX_CODE_LEN:
        raise ValueError(f"SKU コードが長すぎる: {cleaned}")
    return cleaned


def round_up_to(value: int, step: int) -> int:
    """value 以上で最小の step の倍数（value が 0 以下なら 0）。step は 1 以上。"""
    if value <= 0:
        return 0
    return -(-value // step) * step


@dataclass(frozen=True)
class Sku:
    """商品。reorder_point は発注点、pack_size は発注の最小ロット（1 以上）。"""

    code: str
    name: str
    unit: str = "個"
    reorder_point: int = 0
    lead_time_days: int = 0
    pack_size: int = 1

    def __post_init__(self) -> None:
        if self.pack_size < 1:
            raise ValueError("pack_size は 1 以上")
        if self.reorder_point < 0 or self.lead_time_days < 0:
            raise ValueError("reorder_point と lead_time_days は 0 以上")


@dataclass
class Catalog:
    """商品マスタ。コードは正規化して保持する。"""

    skus: dict[str, Sku] = field(default_factory=dict)

    def add(self, sku: Sku) -> Sku:
        """登録して、正規化したコードの Sku を返す。同じコードが既にあれば KeyError。"""
        code = normalize_code(sku.code)
        if code in self.skus:
            raise KeyError(f"登録済みの SKU: {code}")
        stored = replace(sku, code=code)
        self.skus[code] = stored
        return stored

    def get(self, code: str) -> Sku:
        """コード（正規化して照合）から Sku を返す。未登録なら KeyError。"""
        key = normalize_code(code)
        if key not in self.skus:
            raise KeyError(f"未登録の SKU: {key}")
        return self.skus[key]

    def find(self, prefix: str) -> list[Sku]:
        """prefix（正規化して照合）で始まるコードの Sku をコード順に返す。空白だけなら全件。"""
        codes = sorted(self.skus)
        if not prefix.strip():
            return [self.skus[c] for c in codes]
        key = normalize_code(prefix)
        return [self.skus[c] for c in codes if c.startswith(key)]
