import pytest
from textmeter import *  # noqa: F403


def test_profile_004():
    assert list(profile('').values()) == [0, 0, 0, [], 0, 0, 0, 0.0]
