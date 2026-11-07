import pytest
from throttlebox import *  # noqa: F403


def test_tokenbucket_reset_006():
    clock = FakeClock()
    bucket = TokenBucket(7.5, 0.5, clock, 2.25)
    clock.advance(4.0)
    assert bucket.reset() == 7.5


def test_tokenbucket_reset_008():
    clock = FakeClock()
    bucket = TokenBucket(6, 1.75, clock)
    assert bucket.reset(0) == 0


def test_tokenbucket_try_acquire_003():
    clock = FakeClock()
    bucket = TokenBucket(7, 2.5, clock, 3)
    assert bucket.try_acquire(3.25) == False


def test_tokenbucket_try_acquire_005():
    clock = FakeClock(17.25)
    bucket = TokenBucket(7, 2.5, clock, 0)
    clock.advance(1.5)
    assert bucket.try_acquire(3.75) == True


def test_tokenbucket_available_001():
    clock = FakeClock()
    bucket = TokenBucket(7, 2.5, clock, 3)
    assert bucket.available() == 3.0


def test_tokenbucket_available_002():
    clock = FakeClock(17.25)
    bucket = TokenBucket(7, 2.5, clock, 0)
    clock.advance(1.5)
    assert bucket.available() == 3.75


def test_tokenbucket_available_006():
    clock = FakeClock()
    bucket = TokenBucket(7, 2.5, clock)
    bucket.try_acquire(4.5)
    assert bucket.available() == 2.5


def test_tokenbucket_time_until_001():
    clock = FakeClock()
    bucket = TokenBucket(7, 2.5, clock)
    assert bucket.time_until(3) == 0.0


def test_tokenbucket_time_until_009():
    clock = FakeClock()
    bucket = TokenBucket(7, 2.5, clock, 0.5)
    assert bucket.time_until() == 0.2
