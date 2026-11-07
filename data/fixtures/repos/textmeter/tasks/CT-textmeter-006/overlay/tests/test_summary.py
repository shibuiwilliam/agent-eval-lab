import pytest
from textmeter import *  # noqa: F403


def test_profile_002():
    assert list(profile('Tiny tiny tiny words, tiny things.', k=2).values()) == [6, 1, 3, [('tiny', 4), ('things', 1)], 2, 6, 4, 17.0]


def test_profile_005():
    assert list(profile('Room 12 and room 7, room 12 again.').values()) == [8, 1, 5, [('room', 3), ('12', 2), ('7', 1), ('again', 1), ('and', 1)], 3, 5, 6, 19.75]


def test_profile_008():
    assert list(profile('one two three four five six seven', k=10).values()) == [7, 1, 7, [('five', 1), ('four', 1), ('one', 1), ('seven', 1), ('six', 1), ('three', 1), ('two', 1)], 7, 5, 6, 14.0]


def test_profile_009():
    assert list(profile('Wonderful mountains everywhere surround wandering travellers.').values()) == [6, 1, 6, [('everywhere', 1), ('mountains', 1), ('surround', 1), ('travellers', 1), ('wandering', 1)], 6, 10, 5, 52.0]


def test_profile_010():
    assert list(profile('a b c a b a', k=2).values()) == [6, 1, 3, [('a', 3), ('b', 2)], 1, 1, 4, 17.0]


def test_profile_013():
    assert list(profile('an cold village carved the science. an narrow forest opened the kitchen.').values()) == [12, 2, 10, [('an', 2), ('the', 2), ('carved', 1), ('cold', 1), ('forest', 1)], 8, 7, 11, 23.67]


def test_profile_014():
    assert list(profile('an long bridge crossed a weather. a heavy window followed a blanket.').values()) == [12, 2, 10, [('a', 3), ('an', 1), ('blanket', 1), ('bridge', 1), ('crossed', 1)], 9, 8, 11, 27.0]
