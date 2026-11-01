import asyncio
import math
from datetime import UTC, date, datetime

import numpy as np
import pytest

from paper_trade import optiondata as od
from paper_trade import update
from paper_trade.market.calendar import MARKET_TZ
from paper_trade.market.clock import SimClock
from paper_trade.market.engine import MarketEngine
from paper_trade.market.service import MarketService
from paper_trade.market.symbols import SYMBOLS
from paper_trade.realprices import Credentials, FetchError
from paper_trade.store import Store

KEYS = Credentials("id", "secret")
START = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)
NOW = datetime(2026, 10, 30, 16, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.t = NOW

    def __call__(self) -> datetime:
        return self.t


def test_parse_earnings_takes_each_symbols_next_report() -> None:
    text = (
        "symbol,name,reportDate,fiscalDateEnding,estimate,currency,timeOfTheDay\n"
        "AAPL,APPLE,2026-11-02,2026-09-30,1.98,USD,post-market\n"
        "AAPL,APPLE,2027-01-29,2026-12-31,2.1,USD,post-market\n"
        "NVDA,NVIDIA,2026-11-18,2026-10-31,2.49,USD,\n"
        "XOM,EXXON,2026-10-29,2026-09-30,3.9,USD,pre-market\n"
        "ABBV,ABBVIE,2026-11-01,2026-09-30,3.7,USD,pre-market\n"
    )
    got = od.parse_earnings(text, ["AAPL", "NVDA", "XOM"], date(2026, 10, 30))
    assert got == {"AAPL": od.Earnings(date(2026, 11, 2), "post-market"), "NVDA": od.Earnings(date(2026, 11, 18), "")}
    assert got["AAPL"].event_day == date(2026, 11, 3)


def test_fetch_chain_follows_pages_and_slows_down_near_the_rate_limit(monkeypatch) -> None:
    pages = [
        ({"snapshots": {"A1": {}}, "next_page_token": "p2"}, {"X-Ratelimit-Remaining": "5", "X-Ratelimit-Reset": "9e9"}),
        ({"snapshots": {"A2": {}}, "next_page_token": None}, {"X-Ratelimit-Remaining": "150", "X-Ratelimit-Reset": "9e9"}),
    ]
    urls = []

    def get(url, headers, timeout):
        urls.append(url)
        return pages[len(urls) - 1]

    monkeypatch.setattr(od, "request_json", get)
    slept = []
    limit = od.RateLimit(reserve=20, sleep=slept.append)
    assert od.fetch_chain("AAPL", KEYS, limit) == {"A1": {}, "A2": {}}
    assert "feed=indicative" in urls[0] and "page_token=p2" in urls[1]
    assert len(slept) == 1 and 0 < slept[0] <= 60  # waited once, before the second page


def test_fetch_open_interest_lists_every_expiration(monkeypatch) -> None:
    seen = []

    def get(url, headers, timeout):
        seen.append(url)
        contracts = [{"symbol": "AAPL261120C00350000", "open_interest": "1200"}, {"symbol": "AAPL261120P00300000"}]
        return {"option_contracts": contracts, "next_page_token": None}, {}

    monkeypatch.setattr(od, "request_json", get)
    assert od.fetch_open_interest("AAPL", KEYS, date(2026, 10, 30), od.RateLimit()) == {"AAPL261120C00350000": 1200}
    assert "paper-api.alpaca.markets" in seen[0] and "expiration_date_lte=2030-" in seen[0]


def test_fetch_bars_pages_and_sorts(monkeypatch) -> None:
    pages = [
        {"bars": {"SPY": [{"t": "2026-10-28T04:00:00Z", "c": 2.0, "v": 20}]}, "next_page_token": "x"},
        {"bars": {"SPY": [{"t": "2026-10-27T04:00:00Z", "c": 1.0, "v": 10}]}, "next_page_token": None},
    ]
    seen = []

    def get(url, headers, timeout):
        seen.append(url)
        return pages[len(seen) - 1], {}

    monkeypatch.setattr(od, "request_json", get)
    bars = od.fetch_bars(["SPY"], KEYS, NOW, od.RateLimit())
    assert bars["SPY"].days == [date(2026, 10, 27), date(2026, 10, 28)]
    assert list(bars["SPY"].closes) == [1.0, 2.0] and "feed=sip" in seen[0] and "adjustment=all" in seen[0]


def test_sessions_for_volume() -> None:
    et = lambda *a: datetime(*a, tzinfo=MARKET_TZ)  # noqa: E731
    assert od.complete_session(et(2026, 10, 30, 12, 0)) == date(2026, 10, 29)
    assert od.complete_session(et(2026, 10, 30, 16, 5)) == date(2026, 10, 30)
    assert od.complete_session(et(2026, 11, 1, 12, 0)) == date(2026, 10, 30)  # Sunday
    assert od.live_session(et(2026, 10, 30, 10, 0)) is None  # too early
    assert od.live_session(et(2026, 10, 30, 12, 0)) == date(2026, 10, 30)
    assert od.live_session(et(2026, 10, 30, 16, 5)) is None


def test_schedule_and_status() -> None:
    clock = Clock()
    data = od.OptionData(Store(":memory:"), KEYS, now=clock)
    assert data.due(["A", "B"]) == ["A", "B"] and data.seconds_until_due(["A", "B"]) == 0
    data.attempt(2)
    data.record("A", {"symbol": "A", "fetched": NOW.isoformat(), "as_of": "2026-10-30", "shape": {"rho": []}})
    assert data.due(["A", "B"]) == ["B"]
    data.record("B", {"symbol": "B", "fetched": NOW.isoformat(), "shape": None})
    assert data.status(["A", "B"])["running"] == {"done": 2, "total": 2}
    data.finished()
    status = data.status(["A", "B"])
    assert status["calibrated"] == 1 and status["newest"] == "2026-10-30" and status["running"] is None
    assert data.seconds_until_due(["A", "B"]) == od.REFRESH_AFTER.total_seconds()
    data.failed("Alpaca says too many requests")
    assert data.seconds_until_due(["A", "B"]) == od.RETRY_AFTER.total_seconds()
    assert data.status(["A", "B"])["last_error"] == "Alpaca says too many requests"
    clock.t += od.REFRESH_AFTER
    assert data.due(["A", "B"]) == ["A", "B"]


def summary(symbol: str, at: datetime) -> dict:
    return {
        "symbol": symbol,
        "fetched": at.isoformat(),
        "as_of": "2026-10-30",
        "shape": {"rho": [[0.1, 0.3]], "eta": 0.8, "gamma": 0.5},
        "premium": [[0.1, 1.2]],
        "pattern": None,
        "volume": None,
        "earnings": None,
    }


NAMES = [spec.symbol for spec in SYMBOLS]


def make_data(monkeypatch, fail_on: str | None = None, broken: str | None = None):
    data = od.OptionData(Store(":memory:"), KEYS, "av-key")
    calls = []

    def summarize(symbol, creds, ctx, now):
        calls.append(symbol)
        assert data.progress is not None and ctx.earnings == {}
        if symbol == fail_on:
            raise FetchError("Alpaca says too many requests; the next refresh will try again.")
        if symbol == broken:
            raise ValueError("bad data")
        return summary(symbol, now)

    monkeypatch.setattr(update, "fetch_bars", lambda symbols, creds, now: {})
    monkeypatch.setattr(update, "fetch_earnings", lambda key, symbols, today: {})
    monkeypatch.setattr(update, "summarize", summarize)
    monkeypatch.setattr(update, "fetch_treasury_rate", lambda key: (date(2026, 10, 28), 0.0418))
    return data, calls


def restarted(data: od.OptionData) -> MarketService:
    """The app after a restart: it reads what the update saved."""
    return MarketService(SimClock(start=START, real_time=lambda: 0.0), MarketEngine(START), data.store, history_days=5, option_data=od.OptionData(data.store, KEYS))


def test_update_calibrates_each_symbol_and_a_restart_reprices_chains(monkeypatch) -> None:
    data, calls = make_data(monkeypatch)
    svc = restarted(data)
    expiry = date.fromisoformat(svc.option_expirations("AAPL")[3]["date"])
    before = svc.option_chain("AAPL", expiry)
    assert before["calibration"]["as_of"] is None
    assert update.update_options(data, NAMES, say=lambda m: None) == []
    assert calls == NAMES
    svc = restarted(data)
    after = svc.option_chain("AAPL", expiry)
    assert after["calibration"]["as_of"] == "2026-10-30" and after["calibration"]["premium"] == 1.2
    assert after["call"]["iv"] != before["call"]["iv"]
    status = data.status(NAMES)
    assert status["calibrated"] == len(NAMES) and status["running"] is None and status["earnings"]
    assert data.rate == pytest.approx(0.0418)  # the interest rate was fetched too
    calls.clear()
    update.update_options(data, NAMES, say=lambda m: None)  # nothing due
    assert calls == []


def test_a_failed_request_stops_the_update_and_keeps_what_was_done(monkeypatch) -> None:
    data, calls = make_data(monkeypatch, fail_on="XLF")
    problems = update.update_options(data, NAMES, say=lambda m: None)
    assert calls == ["SPY", "QQQ", "IWM", "XLF"]
    assert set(data.saved) == {"SPY", "QQQ", "IWM"}
    assert "XLF's options: Alpaca says too many requests" in problems[0]
    assert "XLF's options: Alpaca says too many requests" in data.status(NAMES)["last_error"]
    svc = restarted(data)
    assert svc.calibration("SPY").calibrated and not svc.calibration("XLF").calibrated


def test_a_symbol_that_cant_be_calibrated_keeps_the_defaults_until_the_next_day(monkeypatch) -> None:
    data, calls = make_data(monkeypatch, broken="GLD")
    problems = update.update_options(data, NAMES, say=lambda m: None)
    assert calls == NAMES and "GLD" in data.saved
    assert not restarted(data).calibration("GLD").calibrated and data.due(NAMES) == []
    assert "GLD's options couldn't be calibrated" in problems[0]


def test_an_unexpected_error_in_the_daily_bars_is_a_failure_to_show(monkeypatch) -> None:
    data, _ = make_data(monkeypatch)

    def boom(*args):
        raise FetchError("Alpaca says no")

    monkeypatch.setattr(update, "fetch_bars", boom)
    problems = update.update_options(data, NAMES, say=lambda m: None)
    assert problems == ["daily bars: Alpaca says no"] and "daily bars" in data.status(NAMES)["last_error"]


def test_without_keys_nothing_is_fetched() -> None:
    data = od.OptionData(Store(":memory:"), None)
    assert "APCA_API_KEY_ID" in update.update_options(data, NAMES, say=lambda m: None)[0]
    svc = MarketService(SimClock(start=START, real_time=lambda: 0.0), MarketEngine(START), data.store, 5, None, data)
    assert svc.calibrations == {}


def test_saved_calibrations_are_used_after_a_restart(monkeypatch) -> None:
    store = Store(":memory:")
    store.set("option_calibration", {"AAPL": summary("AAPL", NOW)})
    svc = MarketService(
        SimClock(start=START, real_time=lambda: 0.0), MarketEngine(START), store, 5, option_data=od.OptionData(store, KEYS)
    )
    assert svc.calibration("AAPL").rho_at("AAPL", 1.0) == 0.3
    expiry = date.fromisoformat(svc.option_expirations("AAPL")[0]["date"])
    assert svc.option_chain("AAPL", expiry)["calibration"]["skew"] == 0.3
    assert np.isfinite(svc.option_chain("AAPL", expiry)["atm_iv"])


def test_rate_limit_headers_ignore_case() -> None:
    slept = []
    limit = od.RateLimit(reserve=20, sleep=slept.append)
    limit.update({"x-ratelimit-remaining": "3", "X-RateLimit-Reset": "9e9"})
    limit.wait()
    assert slept == [60]


def test_treasury_rate_is_the_latest_value_made_continuous() -> None:
    body = '{"data": [{"date": "2026-10-29", "value": "."}, {"date": "2026-10-28", "value": "4.18"}]}'
    day, rate = od.parse_treasury_rate(body)
    assert day == date(2026, 10, 28)
    assert rate == pytest.approx(2 * math.log(1 + 0.0418 / 2), abs=1e-6)


def test_treasury_rate_rejects_a_note() -> None:
    with pytest.raises(FetchError):
        od.parse_treasury_rate('{"Information": "rate limit"}')


def test_calibrations_carry_the_rate() -> None:
    from paper_trade.market import options

    cals = options.calibrations({"SPY": {"symbol": "SPY", "fetched": "2026-10-28T00:00:00+00:00", "shape": None}}, 0.0415)
    assert cals["SPY"].rate == 0.0415
    assert options.DEFAULT.rate == options.RATE
