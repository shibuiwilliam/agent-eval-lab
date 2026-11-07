import pytest
from textmeter import *  # noqa: F403


def test_profile_008():
    assert list(profile('one two three four five six seven', k=10).values()) == [7, 1, 7, [('five', 1), ('four', 1), ('one', 1), ('seven', 1), ('six', 1), ('three', 1), ('two', 1)], 7, 5, 6, 14.0]


def test_profile_013():
    assert list(profile('an cold village carved the science. an narrow forest opened the kitchen.').values()) == [12, 2, 10, [('an', 2), ('the', 2), ('carved', 1), ('cold', 1), ('forest', 1)], 8, 7, 11, 23.67]
