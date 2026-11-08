"""受払台帳。入庫・出庫・調整の記録と、手持ち数・履歴の照会。"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from decimal import Decimal

from binkeeper.catalog import normalize_code

KINDS = ("in", "out", "adjust")


@dataclass(frozen=True)
class Movement:
    """1 件の受払。seq は台帳が記録時に 1 から振る通し番号（作る側は 0 のままでよい）。"""

    sku: str
    kind: str
    qty: int
    unit_cost: Decimal | None = None
    seq: int = 0


@dataclass
class Layer:
    """入庫ごとの原価層。qty は残数、cost は単位原価。"""

    qty: int
    cost: Decimal


def consume_layers(layers: list[Layer], qty: int) -> Decimal:
    """先頭の層から qty 個を取り出して消費し、その原価の合計を返す。足りなければ ValueError。"""
    total = Decimal(0)
    need = qty
    while need > 0:
        if not layers:
            raise ValueError("原価層が足りない")
        take = min(need, layers[0].qty)
        total += layers[0].cost * take
        layers[0].qty -= take
        need -= take
        if layers[0].qty == 0:
            layers.pop(0)
    return total


def peek_layers(layers: list[Layer], qty: int) -> Decimal:
    """層を消費せずに、先頭から qty 個の原価の見積り。足りない分は最後の層の単位原価で数える。"""
    total = Decimal(0)
    need = qty
    for layer in layers:
        take = min(need, layer.qty)
        total += layer.cost * take
        need -= take
        if need <= 0:
            break
    if need > 0 and layers:
        total += layers[-1].cost * need
    return total


def _signed_qty(m: Movement) -> int:
    """在庫への効き。入庫は +qty、出庫は -qty、調整は qty（符号付き）。"""
    if m.kind == "out":
        return -m.qty
    return m.qty


@dataclass
class Ledger:
    """受払台帳。allow_negative が偽なら、手持ちが負になる記録を拒否する。"""

    allow_negative: bool = False
    movements: list[Movement] = field(default_factory=list)

    def on_hand(self, sku: str) -> int:
        """手持ち数（その SKU の受払の効きの合計）。記録が無ければ 0。"""
        code = normalize_code(sku)
        return sum(_signed_qty(m) for m in self.movements if m.sku == code)

    def record(self, m: Movement) -> Movement:
        """受払を記録し、通し番号と正規化したコードを付けた Movement を返す。

        種別が in・out で数量が 0 以下、adjust で数量が 0、未知の種別は ValueError。
        手持ちが負になる記録は、allow_negative が偽なら ValueError（ちょうど 0 になるのは可）。
        """
        if m.kind not in KINDS:
            raise ValueError(f"未知の種別: {m.kind}")
        if m.kind != "adjust" and m.qty <= 0:
            raise ValueError("in・out の数量は正")
        if m.kind == "adjust" and m.qty == 0:
            raise ValueError("adjust の数量は 0 以外")
        code = normalize_code(m.sku)
        if not self.allow_negative and self.on_hand(code) + _signed_qty(m) < 0:
            raise ValueError("手持ちが負になる")
        stored = replace(m, sku=code, seq=len(self.movements) + 1)
        self.movements.append(stored)
        return stored

    def receive(self, sku: str, qty: int, unit_cost: Decimal | None = None) -> Movement:
        """入庫の記録（`record(Movement(sku, "in", qty, unit_cost))` と同じ）。"""
        return self.record(Movement(sku, "in", qty, unit_cost))

    def issue(self, sku: str, qty: int) -> Movement:
        """出庫の記録（`record(Movement(sku, "out", qty))` と同じ）。"""
        return self.record(Movement(sku, "out", qty))

    def adjust(self, sku: str, qty: int, unit_cost: Decimal | None = None) -> Movement:
        """調整の記録（`record(Movement(sku, "adjust", qty, unit_cost))` と同じ。qty は符号付き）。"""
        return self.record(Movement(sku, "adjust", qty, unit_cost))

    def history(self, sku: str, since_seq: int | None = None) -> list[Movement]:
        """その SKU の受払を記録順に返す。since_seq を渡すと、それより後の通し番号だけ。"""
        code = normalize_code(sku)
        rows = [m for m in self.movements if m.sku == code]
        if since_seq is None:
            return rows
        return [m for m in rows if m.seq > since_seq]

    def layers(self, sku: str) -> list[Layer]:
        """受払を記録順に再生して、いま残っている FIFO の原価層を作る。

        入庫と正の調整は層を足す（単位原価が None なら 0）。出庫と負の調整は先頭の層から消費する。
        """
        layers: list[Layer] = []
        for m in self.history(sku):
            if m.kind == "out" or (m.kind == "adjust" and m.qty < 0):
                consume_layers(layers, abs(m.qty))
            else:
                cost = m.unit_cost if m.unit_cost is not None else Decimal(0)
                layers.append(Layer(m.qty, cost))
        return layers
