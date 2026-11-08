import pytest
from textmeter import *  # noqa: F403


def test_profile_002():
    assert list(profile('Tiny tiny tiny words, tiny things.', k=2).values()) == [6, 1, 3, [('tiny', 4), ('things', 1)], 2, 6, 4, 17.0]


def test_profile_011():
    assert list(profile('an old blanket carried a morning. a old science repaired the library.').values()) == [12, 2, 10, [('a', 2), ('old', 2), ('an', 1), ('blanket', 1), ('carried', 1)], 8, 8, 11, 33.67]
