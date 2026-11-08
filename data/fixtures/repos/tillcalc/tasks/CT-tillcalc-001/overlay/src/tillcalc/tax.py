"""消費税の計算（税抜・税込の相互変換と累進課税）。"""

from __future__ import annotations

from decimal import Decimal

from tillcalc.currency import CurrencyError
from tillcalc.money import Money, _to_decimal
from tillcalc.rounding import RoundingMode, bracket_slices, round_minor, tax_part


def tax_portion(
    gross: Money, rate: Decimal | str | int, mode: RoundingMode = RoundingMode.HALF_UP
) -> Money:
    """税込金額 gross に含まれる税額を返す。端数は mode（既定は 0 から遠い方へ丸める HALF_UP）。"""
    raw = tax_part(gross.amount, _to_decimal(rate))
    return Money(round_minor(raw, gross.currency, mode), gross.currency)


def add_tax(
    amount: Money,
    rate: Decimal | str | int,
    inclusive: bool = False,
    mode: RoundingMode = RoundingMode.HALF_UP,
) -> tuple[Money, Money, Money]:
    """(税抜, 税, 税込) を返す。

    inclusive=False なら amount を税抜として税を足す。inclusive=True なら amount を税込として
    税を取り出す。税は mode（既定 HALF_UP）で最小単位に丸める。税率は 0.10 のような小数。
    """
    dec_rate = _to_decimal(rate)
    if dec_rate < 0:
        raise CurrencyError("税率は 0 以上")
    if inclusive:
        tax = tax_portion(amount, dec_rate, mode)
        return amount - tax, tax, amount
    tax = amount.times(dec_rate, mode)
    return amount, tax, amount + tax


def progressive_tax(
    income: Money, brackets: list[tuple[Decimal | str | int | None, Decimal | str | int]]
) -> Money:
    """累進課税。brackets は (上限, 税率) を上限の昇順に並べたもの。上限 None は最上段。

    各段には、前の段の上限から自段の上限までの部分だけに税率を掛ける。合計を最後に
    一度だけ偶数丸めで最小単位に丸める。上限が昇順でなければ CurrencyError。
    """
    parsed = [(None if u is None else _to_decimal(u), _to_decimal(r)) for u, r in brackets]
    total = sum((width / rate for width, rate in bracket_slices(income.amount, parsed)), Decimal(0))
    return Money(round_minor(total, income.currency), income.currency)


def marginal_rate(
    income: Money, brackets: list[tuple[Decimal | str | int | None, Decimal | str | int]]
) -> Decimal:
    """所得の最後の 1 単位に掛かる税率（所得が 0 以下なら 0）。brackets は progressive_tax と同じ。"""
    parsed = [(None if u is None else _to_decimal(u), _to_decimal(r)) for u, r in brackets]
    rate = Decimal(0)
    for width, tier_rate in bracket_slices(income.amount, parsed):
        if width > 0:
            rate = tier_rate
    return rate
