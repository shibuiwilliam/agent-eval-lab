import pytest
from decimal import Decimal
from tillcalc import *  # noqa: F403


def test_allocate_003():
    assert [m.to_str() for m in allocate(Money.of('0.05', 'USD'), [3, 7])] == ['0.02', '0.03']


def test_allocate_011():
    with pytest.raises(CurrencyError):
        allocate(Money.of('10', 'USD'), [])


def test_allocate_012():
    with pytest.raises(CurrencyError):
        allocate(Money.of('10', 'USD'), [0, 0])


def test_allocate_013():
    with pytest.raises(CurrencyError):
        allocate(Money.of('10', 'USD'), [1, -1])


def test_split_even_005():
    assert [m.to_str() for m in split_even(Money.of('20.00', 'USD'), 4)] == ['5.00', '5.00', '5.00', '5.00']


def test_split_even_006():
    assert [m.to_str() for m in split_even(Money.of('7.77', 'USD'), 1)] == ['7.77']


def test_split_even_008():
    assert [m.to_str() for m in split_even(Money.of('1000', 'JPY'), 7)] == ['143', '143', '143', '143', '143', '143', '142']
