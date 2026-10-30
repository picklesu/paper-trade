from datetime import date, datetime, timedelta

from paper_trade.market.calendar import (
    MARKET_TZ,
    SESSION_SECONDS,
    add_trading_seconds,
    is_trading_day,
    next_trading_day,
    nyse_holidays,
    previous_trading_day,
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


def test_previous_trading_day() -> None:
    from paper_trade.market.calendar import previous_trading_day

    assert previous_trading_day(date(2026, 1, 12)) == date(2026, 1, 9)  # Monday -> Friday
    assert previous_trading_day(date(2026, 1, 7)) == date(2026, 1, 6)


def test_nyse_holidays_match_the_published_calendars() -> None:
    assert [d.isoformat() for d in nyse_holidays(2026)] == [
        "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
        "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
    ]  # fmt: skip
    # Weekend rules: Saturday holidays move to Friday, Sunday ones to Monday; but a
    # Saturday New Year's Day isn't observed. Juneteenth since 2022.
    assert date(2027, 12, 24) in nyse_holidays(2027) and date(2027, 7, 5) in nyse_holidays(2027)
    assert date(2021, 12, 31) not in nyse_holidays(2021) and date(2022, 1, 1) not in nyse_holidays(2022)
    assert all(d.month != 6 for d in nyse_holidays(2021))
    assert date(2027, 3, 26) in nyse_holidays(2027)  # Good Friday


def test_holidays_are_skipped() -> None:
    assert not is_trading_day(date(2026, 11, 26))
    assert next_trading_day(date(2026, 11, 25)) == date(2026, 11, 27)
    assert previous_trading_day(date(2026, 1, 20)) == date(2026, 1, 16)  # over MLK Day
    assert snap(et(2026, 7, 3, 12, 0)) == et(2026, 7, 6, 9, 30)
    # The close before a holiday weekend continues at the next open.
    assert add_trading_seconds(et(2026, 4, 2, 15, 59), 60) == et(2026, 4, 6, 9, 30)
    assert trading_days_between(date(2026, 12, 23), date(2026, 12, 28)) == 2  # Dec 24 and 28
