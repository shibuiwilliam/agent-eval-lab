import pytest
from decimal import Decimal
from tillcalc import *  # noqa: F403


def test_allocate_010():
    assert [m.to_str() for m in allocate(Money.of('214.74', 'USD'), [5, 2, 7, 1])] == ['71.58', '28.63', '100.21', '14.32']


def test_split_even_008():
    assert [m.to_str() for m in split_even(Money.of('1000', 'JPY'), 7)] == ['143', '143', '143', '143', '143', '143', '142']
