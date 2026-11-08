import pytest
from decimal import Decimal
from tillcalc import *  # noqa: F403


def test_allocate_004():
    assert [m.to_str() for m in allocate(Money.of('10.01', 'USD'), [1, 1])] == ['5.01', '5.00']


def test_allocate_008():
    assert [m.to_str() for m in allocate(Money.of('12.345', 'KWD'), [7, 2, 1])] == ['8.642', '2.469', '1.234']


def test_allocate_011():
    with pytest.raises(CurrencyError):
        allocate(Money.of('10', 'USD'), [])


def test_allocate_012():
    with pytest.raises(CurrencyError):
        allocate(Money.of('10', 'USD'), [0, 0])


def test_allocate_013():
    with pytest.raises(CurrencyError):
        allocate(Money.of('10', 'USD'), [1, -1])


def test_split_even_010():
    with pytest.raises(CurrencyError):
        split_even(Money.of('10', 'USD'), 0)
