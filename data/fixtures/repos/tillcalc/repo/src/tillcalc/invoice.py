"""請求書。明細ごと、または請求書ごとに税を計算する。"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from tillcalc.currency import CurrencyError
from tillcalc.money import Money
from tillcalc.tax import add_tax


@dataclass(frozen=True)
class Line:
    """請求の明細。discount_pct は百分率（15 なら 15% 引き）で、None は値引きなし。"""

    desc: str
    qty: int
    unit_price: Money
    discount_pct: Decimal | None = None


@dataclass
class Invoice:
    """請求書。rounding は "per_line"（明細ごとに税を丸める）か "per_invoice"（合計に税を掛ける）。"""

    currency: str
    lines: list[Line] = field(default_factory=list)
    tax_rate: Decimal = Decimal("0.10")
    rounding: str = "per_line"

    def _line_total(self, line: Line) -> Money:
        if line.unit_price.currency != self.currency:
            raise CurrencyError("明細の通貨が請求書と違う")
        return Money.line(line.unit_price, line.qty, line.discount_pct)

    def subtotal(self) -> Money:
        """明細ごとに（単価 × 数量 − 値引き）を丸めて合計した税抜小計。"""
        total = Money.zero(self.currency)
        for line in self.lines:
            total = total + self._line_total(line)
        return total

    def tax_total(self) -> Money:
        """消費税の合計。per_line は明細ごとの税の和、per_invoice は小計に対する税。"""
        if self.rounding == "per_invoice":
            return add_tax(self.subtotal(), self.tax_rate)[1]
        total = Money.zero(self.currency)
        for line in self.lines:
            total = total + add_tax(self._line_total(line), self.tax_rate)[1]
        return total

    def grand_total(self) -> Money:
        """税込合計（小計 + 消費税）。"""
        return self.subtotal() + self.tax_total()
