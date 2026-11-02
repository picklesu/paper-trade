from datetime import UTC, date, datetime

import pytest

from paper_trade import intraday, update
from paper_trade.intraday import SLOTS, Intraday, fetch_minutes, parse_minutes, slot_of
from paper_trade.optiondata import RateLimit
from paper_trade.realprices import Credentials, FetchError
from paper_trade.store import Store

CREDS = Credentials("id", "secret")
NOW = datetime(2026, 11, 2, 15, 0, tzinfo=UTC)


def bar(stamp: str, volume: int, close: float = 100.0) -> dict:
    return {"t": stamp, "v": volume, "c": close}


def test_slots_are_new_york_minutes_from_the_open_to_the_close() -> None:
    assert slot_of("2026-10-22T13:30:00Z") == (date(2026, 10, 22), 0)  # 9:30 EDT
    assert slot_of("2026-10-22T19:59:00Z") == (date(2026, 10, 22), 389)
    assert slot_of("2026-10-22T20:00:00Z") == (date(2026, 10, 22), 390)  # the closing cross
    assert slot_of("2026-10-22T20:01:00Z") is None  # after hours
    assert slot_of("2026-10-22T13:29:00Z") is None  # pre-market
    assert slot_of("2026-12-01T14:30:00Z") == (date(2026, 12, 1), 0)  # 9:30 EST


def test_parse_keeps_regular_session_bars_by_day() -> None:
    days = parse_minutes([bar("2026-10-22T13:00:00Z", 5), bar("2026-10-22T13:30:00Z", 7, 101.5), bar("2026-10-22T20:00:00Z", 9)])
    volumes, closes = days[date(2026, 10, 22)]
    assert (volumes[0], closes[0], volumes[390], len(volumes)) == (7, 101.5, 9, SLOTS)
    assert sum(volumes) == 16


def test_fetch_pages_and_drops_thin_days(monkeypatch) -> None:
    full = [bar(f"2026-10-21T{13 + (30 + m) // 60}:{(30 + m) % 60:02d}:00Z", 10) for m in range(390)]
    pages = iter([
        ({"bars": {"SPY": full}, "next_page_token": "t"}, {}),
        ({"bars": {"SPY": [bar("2026-10-22T13:30:00Z", 5)]}}, {}),
    ])
    urls = []

    def fake(url, headers, timeout):
        urls.append(url)
        return next(pages)

    monkeypatch.setattr(intraday, "request_json", fake)
    days = fetch_minutes("SPY", date(2026, 10, 21), NOW, CREDS, RateLimit())
    assert list(days) == [date(2026, 10, 21)]  # the day with one minute is dropped
    assert "page_token=t" in urls[1] and "timeframe=1Min" in urls[0] and "feed=sip" in urls[0]


def test_update_saves_finished_days_and_resumes(monkeypatch) -> None:
    store = Store(":memory:")
    calls = []

    def fake(symbol, since, now, creds):
        calls.append(since)
        v = [3] * SLOTS
        return {date(2026, 10, 21): (v, [1.0] * SLOTS), date(2026, 11, 2): (v, [1.0] * SLOTS)}

    minutes = Intraday(store, CREDS, fake)
    assert minutes.update("SPY", NOW) == 1  # Nov 2 is still being traded (10:00 New York)
    assert minutes.last_day("SPY") == date(2026, 10, 21)
    minutes.update("SPY", NOW)
    assert calls == [intraday.FIRST_DAY, date(2026, 10, 21)]
    (day, volumes, closes), = minutes.days("SPY")
    assert (day, sum(volumes), closes[0]) == (date(2026, 10, 21), 3 * SLOTS, 1.0)


def test_update_without_keys_says_so() -> None:
    with pytest.raises(FetchError, match="Alpaca keys"):
        Intraday(Store(":memory:"), None).update("SPY", NOW)
    assert "no Alpaca keys" in update.update_intraday(Intraday(Store(":memory:"), None), lambda m: None)[0]


def test_the_default_update_leaves_intraday_out() -> None:
    assert "intraday" not in update.STEPS and "intraday" in update.ALL_STEPS
