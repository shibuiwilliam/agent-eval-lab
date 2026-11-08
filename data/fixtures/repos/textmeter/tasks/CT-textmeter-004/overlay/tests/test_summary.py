import pytest
from textmeter import *  # noqa: F403


def test_profile_002():
    assert list(profile('Tiny tiny tiny words, tiny things.', k=2).values()) == [6, 1, 3, [('tiny', 4), ('things', 1)], 2, 6, 4, 17.0]


def test_profile_003():
    assert list(profile('Alpha beta Gamma. Alpha BETA gamma delta! Epsilon?', k=3).values()) == [8, 3, 5, [('alpha', 2), ('beta', 2), ('gamma', 2)], 2, 7, 5, 14.09]
