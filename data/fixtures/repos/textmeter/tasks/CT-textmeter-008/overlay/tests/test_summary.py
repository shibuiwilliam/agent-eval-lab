import pytest
from textmeter import *  # noqa: F403


def test_profile_009():
    assert list(profile('Wonderful mountains everywhere surround wandering travellers.').values()) == [6, 1, 6, [('everywhere', 1), ('mountains', 1), ('surround', 1), ('travellers', 1), ('wandering', 1)], 6, 10, 5, 52.0]


def test_profile_014():
    assert list(profile('an long bridge crossed a weather. a heavy window followed a blanket.').values()) == [12, 2, 10, [('a', 3), ('an', 1), ('blanket', 1), ('bridge', 1), ('crossed', 1)], 9, 8, 11, 27.0]
