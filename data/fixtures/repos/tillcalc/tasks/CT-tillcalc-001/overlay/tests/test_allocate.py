import pytest
from decimal import Decimal
from tillcalc import *  # noqa: F403


def test_allocate_006():
    assert [m.to_str() for m in allocate(Money.of('-100.00', 'USD'), [1, 1, 1])] == ['-33.34', '-33.33', '-33.33']


def test_split_even_011():
    assert [m.to_str() for m in split_even(Money.of('100.00', 'USD'), 3)] == ['33.34', '33.33', '33.33']
