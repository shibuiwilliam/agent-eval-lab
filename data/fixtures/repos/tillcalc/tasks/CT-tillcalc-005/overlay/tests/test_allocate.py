import pytest
from decimal import Decimal
from tillcalc import *  # noqa: F403


def test_allocate_001():
    assert [m.to_str() for m in allocate(Money.of('100.00', 'USD'), [1, 1, 1])] == ['33.34', '33.33', '33.33']


def test_allocate_013():
    with pytest.raises(CurrencyError):
        allocate(Money.of('10', 'USD'), [1, -1])


def test_split_even_001():
    assert [m.to_str() for m in split_even(Money.of('100.00', 'USD'), 3)] == ['33.34', '33.33', '33.33']


def test_split_even_008():
    assert [m.to_str() for m in split_even(Money.of('1000', 'JPY'), 7)] == ['143', '143', '143', '143', '143', '143', '142']
