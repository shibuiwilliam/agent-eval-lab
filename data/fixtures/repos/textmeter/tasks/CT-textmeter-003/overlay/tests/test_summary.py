import pytest
from textmeter import *  # noqa: F403


def test_profile_003():
    assert list(profile('Alpha beta Gamma. Alpha BETA gamma delta! Epsilon?', k=3).values()) == [8, 3, 5, [('alpha', 2), ('beta', 2), ('gamma', 2)], 2, 7, 5, 14.09]


def test_profile_011():
    assert list(profile('an old blanket carried a morning. a old science repaired the library.').values()) == [12, 2, 10, [('a', 2), ('old', 2), ('an', 1), ('blanket', 1), ('carried', 1)], 8, 8, 11, 33.67]
