"""FIFO の原価層から出す売上原価・在庫評価額・平均原価。"""

from __future__ import annotations

import decimal
from decimal import Decimal

from binkeeper.ledger import Ledger, consume_layers, peek_layers


def round_cost(value: Decimal) -> Decimal:
    """原価を小数 2 桁に丸める（同点は 0 から遠い方）。"""
    return value.quantize(Decimal("0.01"), rounding=decimal.ROUND_HALF_EVEN)


def cogs(ledger: Ledger, sku: str, qty: int) -> Decimal:
    """次に qty 個出庫したときの売上原価（FIFO）。qty が 0 以下、または手持ちを超えれば ValueError。"""
    if qty <= 0:
        raise ValueError("qty は正")
    return consume_layers(ledger.layers(sku), qty)


def inventory_value(ledger: Ledger, sku: str) -> Decimal:
    """在庫評価額（残っている原価層の 単位原価 x 残数 の合計。丸めない）。"""
    layers = ledger.layers(sku)
    return consume_layers(layers, sum(layer.qty for layer in layers))


def average_cost(ledger: Ledger, sku: str) -> Decimal:
    """平均原価 = 在庫評価額 / 手持ち数 を小数 2 桁に丸めたもの。手持ちが 0 以下なら 0.00。"""
    on_hand = ledger.on_hand(sku)
    if on_hand <= 0:
        return Decimal("0.00")
    return round_cost(inventory_value(ledger, sku) / on_hand)


def next_cost(ledger: Ledger, sku: str) -> Decimal | None:
    """次に出庫される 1 個の単位原価（FIFO の先頭の層）。在庫が無ければ None。"""
    layers = ledger.layers(sku)
    if not layers:
        return None
    return peek_layers(layers, 1)
