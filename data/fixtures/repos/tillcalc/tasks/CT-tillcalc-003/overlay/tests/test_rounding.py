import pytest
from decimal import Decimal
from tillcalc import *  # noqa: F403


def test_round_to_step_011():
    with pytest.raises(CurrencyError):
        round_to_step('1', 0)
