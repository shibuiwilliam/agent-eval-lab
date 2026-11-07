"""引当（受注のための在庫の確保）。"""

from __future__ import annotations

from dataclasses import dataclass, field

from binkeeper.catalog import normalize_code
from binkeeper.ledger import Ledger


@dataclass
class Reservations:
    """引当の管理。holds は 受注 ID -> [(SKU コード, 数量)]。"""

    ledger: Ledger
    holds: dict[str, list[tuple[str, int]]] = field(default_factory=dict)

    def reserved(self, sku: str) -> int:
        """その SKU の引当済みの数量（全受注の合計）。"""
        code = normalize_code(sku)
        return sum(q for lines in self.holds.values() for s, q in lines if s == code)

    def available(self, sku: str) -> int:
        """引当可能数 = 手持ち数 - 引当済み。"""
        return self.ledger.on_hand(sku) - self.reserved(sku)

    def reserve(self, sku: str, qty: int, order_id: str) -> int:
        """引き当てて、引当後の引当可能数を返す。

        qty が 0 以下、または引当可能数を超えるなら ValueError（ちょうど引当可能数なら可）。
        同じ受注 ID に複数回引き当てると行が増える。
        """
        if qty <= 0:
            raise ValueError("引当数量は正")
        code = normalize_code(sku)
        left = self.available(code)
        if qty > left:
            raise ValueError(f"引当可能数を超える: {qty} > {left}")
        self.holds.setdefault(order_id, []).append((code, qty))
        return left - qty

    def release(self, order_id: str) -> int:
        """受注の引当をすべて解除し、解除した数量の合計を返す。未知の受注 ID は 0。"""
        lines = self.holds.pop(order_id, [])
        return sum(q for _, q in lines)
