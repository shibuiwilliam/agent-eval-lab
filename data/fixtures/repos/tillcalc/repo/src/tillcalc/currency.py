"""通貨表と最小単位（小数桁数）。"""

from __future__ import annotations

from decimal import Decimal


class CurrencyError(ValueError):
    """未知の通貨コード、または通貨の食い違い。"""


# 通貨コード -> 小数桁数（ISO 4217 の補助単位の桁数）
_EXPONENTS: dict[str, int] = {
    "JPY": 0,
    "KRW": 0,
    "VND": 0,
    "USD": 2,
    "EUR": 2,
    "GBP": 2,
    "CHF": 2,
    "AUD": 2,
    "KWD": 3,
    "BHD": 3,
    "TND": 3,
}


def exponent(code: str) -> int:
    """通貨の小数桁数を返す。未知の通貨は CurrencyError。"""
    try:
        return _EXPONENTS[code]
    except KeyError:
        raise CurrencyError(f"未知の通貨: {code!r}") from None


def quantum(code: str) -> Decimal:
    """通貨の最小単位（JPY なら 1、USD なら 0.01、KWD なら 0.001）。"""
    return Decimal(1).scaleb(-exponent(code))


def minor_units(amount: Decimal, code: str) -> int:
    """金額を最小単位の個数にする。最小単位に満たない端数があれば CurrencyError。"""
    scaled = amount.scaleb(exponent(code))
    if scaled != scaled.to_integral_value():
        raise CurrencyError(f"{code} の最小単位に満たない端数がある: {amount}")
    return int(scaled)


def known_codes() -> list[str]:
    """登録済みの通貨コードを昇順で返す。"""
    return sorted(_EXPONENTS)
