from datetime import date, datetime, timedelta

from paper_trade.market.calendar import (
    MARKET_TZ,
    SESSION_SECONDS,
    add_trading_seconds,
    next_trading_day,
    session_seconds,
    snap,
    trading_days_between,
)


def et(*args: int) -> datetime:
    return datetime(*args, tzinfo=MARKET_TZ)


def test_snap_inside_session_is_unchanged() -> None:
    assert snap(et(2026, 1, 6, 11, 15)) == et(2026, 1, 6, 11, 15)


def test_snap_before_open_goes_to_open() -> None:
    assert snap(et(2026, 1, 6, 7, 0)) == et(2026, 1, 6, 9, 30)


def test_snap_at_or_after_close_goes_to_next_open() -> None:
    assert snap(et(2026, 1, 6, 16, 0)) == et(2026, 1, 7, 9, 30)
    assert snap(et(2026, 1, 6, 22, 0)) == et(2026, 1, 7, 9, 30)


def test_snap_weekend_goes_to_monday() -> None:
    assert snap(et(2026, 1, 9, 17, 0)) == et(2026, 1, 12, 9, 30)  # Friday evening
    assert snap(et(2026, 1, 10, 12, 0)) == et(2026, 1, 12, 9, 30)  # Saturday


def test_next_trading_day_skips_weekend() -> None:
    assert next_trading_day(date(2026, 1, 9)) == date(2026, 1, 12)


def test_add_trading_seconds_within_session() -> None:
    assert add_trading_seconds(et(2026, 1, 6, 10, 0), 90) == et(2026, 1, 6, 10, 1, 30)


def test_add_trading_seconds_crosses_close_into_next_open() -> None:
    # 10 minutes before Friday's close + 15 minutes = Monday 9:35.
    assert add_trading_seconds(et(2026, 1, 9, 15, 50), 15 * 60) == et(2026, 1, 12, 9, 35)


def test_add_trading_seconds_reaching_close_lands_on_next_open() -> None:
    assert add_trading_seconds(et(2026, 1, 6, 15, 59), 60) == et(2026, 1, 7, 9, 30)


def test_add_full_sessions() -> None:
    assert add_trading_seconds(et(2026, 1, 6, 10, 0), 3 * SESSION_SECONDS) == et(2026, 1, 9, 10, 0)


def test_dst_change_keeps_930_open() -> None:
    # US clocks spring forward on Sunday 2026-03-08.
    friday = et(2026, 3, 6, 15, 59)
    monday = add_trading_seconds(friday, 120)
    assert monday == et(2026, 3, 9, 9, 31)
    assert monday - friday != timedelta(days=2, hours=17, minutes=32)  # one hour shorter in UTC


def test_session_seconds() -> None:
    assert session_seconds(et(2026, 1, 6, 9, 31)) == 60


def test_trading_days_between() -> None:
    assert trading_days_between(date(2026, 1, 9), date(2026, 1, 12)) == 1
    assert trading_days_between(date(2026, 1, 5), date(2026, 1, 16)) == 9
    assert trading_days_between(date(2026, 1, 5), date(2026, 1, 5)) == 0


def test_next_trading_day_from_weekend() -> None:
    assert next_trading_day(date(2026, 1, 10)) == date(2026, 1, 12)  # Saturday -> Monday


def test_add_trading_seconds_far_ahead_is_exact_and_fast() -> None:
    import time

    began = time.perf_counter()
    t = add_trading_seconds(et(2026, 1, 6, 10, 0), 252 * 50 * SESSION_SECONDS)
    assert time.perf_counter() - began < 0.01
    assert t.astimezone(MARKET_TZ).time().isoformat() == "10:00:00"
    assert t.weekday() < 5


def test_trading_days_between_far_apart_is_fast() -> None:
    import time

    began = time.perf_counter()
    assert trading_days_between(date(2026, 1, 2), date(2126, 1, 2)) > 25_000
    assert time.perf_counter() - began < 0.01
