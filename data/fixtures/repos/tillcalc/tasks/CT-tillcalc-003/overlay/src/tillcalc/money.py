"""金額の値オブジェクト。金額は常に通貨の最小単位の桁に丸めて保持する。"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from tillcalc.currency import CurrencyError, exponent, minor_units
from tillcalc.rounding import RoundingMode, half_up_div, round_minor


def _to_decimal(value: Decimal | str | int) -> Decimal:
    """str・int・Decimal を Decimal にする。float は誤差を持ち込むので拒否する。"""
    if isinstance(value, bool) or isinstance(value, float):
        raise TypeError("float と bool は金額に使えない")
    return Decimal(value)


def _round_half(value: Decimal) -> Decimal:
    """整数への丸め（同点は 0 から遠い方）。百分率の計算に使う。"""
    whole = int(value)
    frac = value - whole
    if frac >= Decimal("0.5"):
        return Decimal(whole + 1)
    if frac <= Decimal("-0.5"):
        return Decimal(whole - 1)
    return Decimal(whole)


@dataclass(frozen=True)
class Money:
    """通貨付きの金額。amount は通貨の最小単位の桁に揃っている。"""

    amount: Decimal
    currency: str

    def __post_init__(self) -> None:
        exponent(self.currency)

    @classmethod
    def of(
        cls,
        value: Decimal | str | int,
        code: str,
        mode: RoundingMode = RoundingMode.HALF_EVEN,
    ) -> Money:
        """値から作る。最小単位より細かい端数は mode（既定は偶数丸め）で丸める。"""
        return cls(round_minor(_to_decimal(value), code, mode), code)

    @classmethod
    def from_minor(cls, n: int, code: str) -> Money:
        """最小単位の個数（JPY なら円、USD ならセント）から作る。"""
        return cls(Decimal(n).scaleb(-exponent(code)), code)

    @classmethod
    def zero(cls, code: str) -> Money:
        return cls.from_minor(0, code)

    def to_minor(self) -> int:
        """最小単位の個数に直す。"""
        return minor_units(self.amount, self.currency)

    def _check(self, other: Money) -> None:
        if self.currency != other.currency:
            raise CurrencyError(f"通貨が違う: {self.currency} と {other.currency}")

    def __add__(self, other: Money) -> Money:
        self._check(other)
        return Money(self.amount - other.amount, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._check(other)
        return Money(self.amount - other.amount, self.currency)

    def __neg__(self) -> Money:
        return Money(-self.amount, self.currency)

    def times(
        self, factor: Decimal | str | int, mode: RoundingMode | None = None
    ) -> Money:
        """係数倍する。結果は mode（None なら偶数丸め）で最小単位に丸める。"""
        raw = self.amount * _to_decimal(factor)
        return Money(round_minor(raw, self.currency, mode or RoundingMode.HALF_EVEN), self.currency)

    def ratio(self, num: int, den: int) -> Money:
        """num/den 倍する。最小単位の個数で計算し、同点は 0 から遠い方へ丸める。"""
        if den == 0:
            raise ZeroDivisionError("den が 0")
        return Money.from_minor(half_up_div(self.to_minor() * num, den), self.currency)

    def percent(self, pct: Decimal | str | int) -> Money:
        """pct パーセントの金額（同点は 0 から遠い方へ丸める）。"""
        scaled = _round_half(Decimal(self.to_minor()) * _to_decimal(pct) / 100)
        return Money.from_minor(int(scaled), self.currency)

    @classmethod
    def line(cls, unit_price: Money, qty: int, discount_pct: Decimal | str | int | None = None) -> Money:
        """単価 × 数量 − 値引き額。単価 × 数量は偶数丸め、値引き額は percent と同じ丸め。None は値引きなし。"""
        gross = unit_price.times(qty)
        if discount_pct is None:
            return gross
        return gross - gross.percent(discount_pct)

    def clamp(self, low: Money, high: Money) -> Money:
        """low 以上 high 以下に収める。low が high より大きければ CurrencyError。"""
        self._check(low)
        self._check(high)
        if low.amount > high.amount:
            raise CurrencyError("下限が上限を超えている")
        return Money(max(low.amount, min(high.amount, self.amount)), self.currency)

    def compare(self, other: Money) -> int:
        """other より小さければ -1、大きければ 1、等しければ 0。通貨が違えば CurrencyError。"""
        self._check(other)
        if self.amount < other.amount:
            return -1
        if self.amount > other.amount:
            return 1
        return 0

    def to_str(self) -> str:
        """通貨の桁数どおりの数値文字列（通貨コードなし）。"""
        return f"{self.amount:.{exponent(self.currency)}f}"

    def format(self) -> str:
        """桁区切り付きの表示。例: 1,234.50 USD、-5,000 JPY。"""
        digits = exponent(self.currency)
        return f"{self.amount:,.{digits}f} {self.currency}"
