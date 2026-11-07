"""丸めと、端数の配り方・税の取り出し・税率区分の切り分けなど、金額計算の数値の核。"""

from __future__ import annotations

from decimal import (
    ROUND_DOWN,
    ROUND_HALF_EVEN,
    ROUND_HALF_UP,
    ROUND_UP,
    Decimal,
)
from enum import Enum

from tillcalc.currency import CurrencyError, quantum


class RoundingMode(Enum):
    """丸めの方式。DOWN は 0 方向、UP は 0 から遠い方向への切り捨て・切り上げ。"""

    HALF_UP = "half_up"
    HALF_EVEN = "half_even"
    DOWN = "down"
    UP = "up"


_DECIMAL_MODES = {
    RoundingMode.HALF_UP: ROUND_HALF_UP,
    RoundingMode.HALF_EVEN: ROUND_HALF_EVEN,
    RoundingMode.DOWN: ROUND_DOWN,
    RoundingMode.UP: ROUND_UP,
}


def round_minor(
    amount: Decimal, code: str, mode: RoundingMode = RoundingMode.HALF_EVEN
) -> Decimal:
    """金額を通貨の最小単位に丸める。既定は銀行丸め（偶数丸め）。

    HALF_UP は同点を 0 から遠い方へ、HALF_EVEN は同点を偶数側へ丸める。
    """
    return amount.quantize(quantum(code), rounding=_DECIMAL_MODES[mode])


def round_to_step(amount: Decimal | str | int, step: Decimal | str) -> Decimal:
    """金額を step の倍数のうち最も近いものへ丸める（現金丸め）。同点は 0 から遠い方。

    例: round_to_step("12.37", "0.05") は 12.35、round_to_step("12.375", "0.05") は 12.40。
    step が 0 以下なら CurrencyError。
    """
    value = Decimal(str(amount))
    unit = Decimal(str(step))
    if unit <= 0:
        raise CurrencyError("step は正の値でなければならない")
    steps = (value / unit).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return steps * unit


def half_up_div(num: int, den: int) -> int:
    """整数の商を、同点を 0 から遠い方へ丸めて返す（den は正でも負でもよい、0 は不可）。"""
    if den < 0:
        num, den = -num, -den
    sign = -1 if num < 0 else 1
    quotient, rest = divmod(abs(num), den)
    if rest * 2 >= den:
        quotient += 1
    return sign * quotient


def _distribute_remainder(base: list[int], remainders: list[int], leftover: int) -> list[int]:
    """余り leftover 個を、剰余の大きい要素から 1 個ずつ配る。剰余が同じなら前の要素が先。"""
    order = sorted(range(len(base)), key=lambda i: (-remainders[i], i))
    shares = list(base)
    for i in order[:leftover]:
        shares[i] += 1
    return shares


def largest_remainder(minor: int, weights: list[int]) -> list[int]:
    """非負の整数 minor を weights の比で配る（最大剰余法）。合計は必ず minor に一致する。"""
    whole = sum(weights)
    base = [minor * w // whole for w in weights]
    remainders = [minor * w % whole for w in weights]
    return _distribute_remainder(remainders, base, minor - sum(base))


def tax_part(gross: Decimal, rate: Decimal) -> Decimal:
    """税込の gross に含まれる税の生の値（丸め前）。gross * rate / (1 + rate)。"""
    return gross * rate / (1 + rate)


def bracket_slices(
    income: Decimal, brackets: list[tuple[Decimal | None, Decimal]]
) -> list[tuple[Decimal, Decimal]]:
    """累進課税の各段に当たる所得の幅を (幅, 税率) の並びで返す。上限が昇順でなければ CurrencyError。"""
    slices: list[tuple[Decimal, Decimal]] = []
    lower = Decimal(0)
    for upper, rate in brackets:
        if upper is not None and upper < lower:
            raise CurrencyError("上限は昇順でなければならない")
        ceiling = income if upper is None else min(income, upper)
        slices.append((max(ceiling - lower, Decimal(0)), rate))
        if upper is None:
            break
        lower = upper
    return slices
