from datetime import datetime, timedelta

import pytest

from paper_trade.market.calendar import MARKET_TZ
from paper_trade.market.clock import SimClock

START = datetime(2026, 1, 5, 9, 30, tzinfo=MARKET_TZ)  # Monday open


class FakeTime:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def make_clock(start: datetime = START, **kwargs) -> tuple[SimClock, FakeTime]:
    real = FakeTime()
    return SimClock(start=start, real_time=real, **kwargs), real


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


def test_start_outside_market_hours_snaps_to_next_open() -> None:
    clock, _ = make_clock(start=datetime(2026, 1, 3, 12, 0, tzinfo=MARKET_TZ))  # Saturday
    assert clock.now() == START


def test_jumps_from_close_to_next_open() -> None:
    friday = datetime(2026, 1, 9, 15, 59, tzinfo=MARKET_TZ)
    clock, real = make_clock(start=friday, speed=60)
    real.t = 2  # two sim-minutes
    assert clock.now() == datetime(2026, 1, 12, 9, 31, tzinfo=MARKET_TZ)


def test_set_time_snaps_and_keeps_pause() -> None:
    clock, real = make_clock(paused=True)
    clock.set_time(datetime(2026, 1, 6, 18, 0, tzinfo=MARKET_TZ))
    real.t = 50
    assert clock.now() == datetime(2026, 1, 7, 9, 30, tzinfo=MARKET_TZ)
    assert clock.paused
