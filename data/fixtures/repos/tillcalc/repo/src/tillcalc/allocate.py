"""最小単位での按分。端数は最大剰余法で配る。"""

from __future__ import annotations

from tillcalc.currency import CurrencyError
from tillcalc.money import Money
from tillcalc.rounding import largest_remainder


def _signed(total: Money, shares: list[int]) -> list[Money]:
    """total の符号を按分結果に戻す。"""
    sign = -1 if total.amount < 0 else 1
    return [Money.from_minor(sign * s, total.currency) for s in shares]


def allocate(total: Money, ratios: list[int]) -> list[Money]:
    """total を ratios の比で按分する。合計は必ず total に一致する。

    端数は最小単位（JPY なら 1 円、USD なら 1 セント）で最大剰余法により配り、
    剰余が同じなら前の要素を優先する。負の total は符号を保って按分する。
    比が空・負、または全部 0 なら CurrencyError。
    """
    if not ratios or any(r < 0 for r in ratios) or sum(ratios) == 0:
        raise CurrencyError("比は 0 以上で、少なくとも 1 つは正でなければならない")
    return _signed(total, largest_remainder(abs(total.to_minor()), ratios))


def split_even(total: Money, n: int) -> list[Money]:
    """total を n 等分する。端数は先頭から 1 最小単位ずつ配る。n < 1 なら CurrencyError。"""
    if n < 1:
        raise CurrencyError("n は 1 以上でなければならない")
    return _signed(total, largest_remainder(abs(total.to_minor()), [1] * n))
