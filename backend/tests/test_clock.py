from datetime import UTC, datetime, timedelta

import pytest

from paper_trade.market.clock import SimClock

START = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)


class FakeTime:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def make_clock(**kwargs) -> tuple[SimClock, FakeTime]:
    real = FakeTime()
    return SimClock(start=START, real_time=real, **kwargs), real


def test_runs_at_real_time_by_default() -> None:
    clock, real = make_clock()
    real.t = 5
    assert clock.now() == START + timedelta(seconds=5)


def test_speed_multiplies_elapsed_time() -> None:
    clock, real = make_clock(speed=60)
    real.t = 2
    assert clock.now() == START + timedelta(minutes=2)


def test_speed_change_keeps_time_already_elapsed() -> None:
    clock, real = make_clock()
    real.t = 10
    clock.set_speed(10)
    real.t = 11
    assert clock.now() == START + timedelta(seconds=20)


def test_pause_freezes_and_resume_continues() -> None:
    clock, real = make_clock()
    real.t = 3
    clock.pause()
    real.t = 100
    assert clock.now() == START + timedelta(seconds=3)
    clock.resume()
    real.t = 101
    assert clock.now() == START + timedelta(seconds=4)


def test_rejects_non_positive_speed() -> None:
    clock, _ = make_clock()
    with pytest.raises(ValueError):
        clock.set_speed(0)
