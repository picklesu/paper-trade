import asyncio
from datetime import UTC, date, datetime, timedelta

import numpy as np
import pytest

from paper_trade.market.clock import SimClock
from paper_trade.market.engine import MarketEngine
from paper_trade.market.service import MarketService
from paper_trade.realprices import (
    Credentials,
    FetchError,
    RealPrices,
    _refusal,
    credentials,
    parse_snapshots,
)
from paper_trade.store import Store

START = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)
KEYS = Credentials("id", "secret")
SNAPSHOTS = {
    "SPY": {
        "latestTrade": {"t": "2026-10-28T19:59:59.987654321Z", "p": 772.31, "s": 100, "x": "V"},
        "dailyBar": {"t": "2026-10-28T04:00:00Z", "o": 770.0, "h": 775.0, "l": 768.0, "c": 772.3, "v": 1000},
    },
    # No trade in the snapshot: the day's close. 00:30 UTC is still the 28th in New York.
    "AAPL": {"latestTrade": None, "dailyBar": {"t": "2026-10-29T00:30:00Z", "c": 270.0}},
}


class Clock:
    def __init__(self) -> None:
        self.t = datetime(2026, 10, 29, 14, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.t


def test_parse_snapshots() -> None:
    expected = {"SPY": (772.31, date(2026, 10, 28)), "AAPL": (270.0, date(2026, 10, 28))}
    assert parse_snapshots(SNAPSHOTS, ["SPY", "AAPL", "XOM"]) == expected  # XOM left out
    assert parse_snapshots({"SPY": None}, ["SPY"]) == {}


def test_refusals_read_as_messages() -> None:
    assert "rejected the API keys (401 unauthorized.)" in str(_refusal(401, b'{"message": "unauthorized."}'))
    assert "rejected the API keys (401)" in str(_refusal(401, b"<html>401 Authorization Required</html>"))
    assert "too many requests" in str(_refusal(429, b"slow down"))
    assert str(_refusal(500, b'{"message": "internal error"}')) == "Alpaca answered 500 internal error"


def test_credentials_from_env_file(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    env = tmp_path / ".env"
    assert credentials(env) is None
    env.write_text("# local\nAPCA_API_KEY_ID = abc123\n")
    assert credentials(env) is None  # both are needed
    env.write_text("# local\nAPCA_API_KEY_ID = abc123\nAPCA_API_SECRET_KEY='s3cret'\n")
    assert credentials(env) == Credentials("abc123", "s3cret")
    monkeypatch.setenv("APCA_API_KEY_ID", "fromenv")
    assert credentials(env) == Credentials("fromenv", "s3cret")


def test_record_saves_prices_and_notes_missing_symbols() -> None:
    clock = Clock()
    store = Store(":memory:")
    real = RealPrices(store, KEYS, now=clock)
    assert real.stale(timedelta(minutes=1))
    real.attempt()
    real.record({"SPY": (772.31, date(2026, 10, 28))}, ["SPY", "XOM"])
    assert real.status(["SPY", "XOM"])["last_error"] == "no price for XOM"
    assert not real.stale(timedelta(minutes=1))
    clock.t += timedelta(minutes=1)
    assert real.stale(timedelta(minutes=1))

    real.record({}, ["SPY"])
    assert real.status(["SPY"])["last_error"] == "Alpaca sent no prices"
    real.record({"SPY": (780.0, date(2026, 10, 29))}, ["SPY"])
    status = real.status(["SPY", "XOM"])
    assert status["last_error"] is None and status["available"] == 1 and status["newest"] == "2026-10-29"
    assert status["fetched"] == clock.t.isoformat()
    # Saved prices survive a restart.
    assert RealPrices(store, KEYS).saved["SPY"].price == 780.0


def test_nothing_is_fetched_without_keys() -> None:
    svc = make_service(RealPrices(Store(":memory:"), None))
    asyncio.run(svc.refresh_real_prices())
    assert svc.real_prices_status()["configured"] is False


def make_service(real: RealPrices | None = None) -> MarketService:
    clock = SimClock(start=START, real_time=lambda: 0.0)
    return MarketService(clock, MarketEngine(START), Store(":memory:"), history_days=5, real=real)


def test_reanchor_makes_prices_equal_the_targets_now() -> None:
    engine = MarketEngine(START)
    engine.advance(START + timedelta(days=2, minutes=37))
    t = engine.time
    targets = np.array([s.price * 1.3 for s in engine.specs])
    engine.reanchor(targets)
    assert engine.time == engine.origin == t and not engine.backfilled
    for q, target in zip(engine.quotes.values(), targets):
        assert q.last == pytest.approx(target, rel=1e-12)


def test_sync_uses_saved_prices_keeps_the_account_and_rebuilds_history() -> None:
    real = RealPrices(Store(":memory:"), KEYS)
    svc = make_service(real)
    real.store = svc.store
    asyncio.run(svc.build_history())
    order = svc.broker.place("AAPL", "buy", 1, "market")
    others = {s: q.last for s, q in svc.engine.quotes.items()}
    real.record({"SPY": (772.31, date(2026, 10, 28)), "AAPL": (270.0, date(2026, 10, 28))}, ["SPY", "AAPL"])

    async def sync() -> dict:
        status = await svc.sync_real_prices()
        assert svc.building_history  # the history is generated again
        q = svc.engine.quotes
        assert q["SPY"].last == pytest.approx(772.31) and q["AAPL"].last == pytest.approx(270.0)
        assert q["XOM"].last == pytest.approx(others["XOM"])  # no real price yet: unchanged
        await svc._history_task
        return status

    status = asyncio.run(sync())
    assert status["available"] == 2
    assert svc.broker.orders[order.id].status == "filled"  # the account is kept
    days = svc.history.stored_days()
    assert len(days) == 5 and max(days) < svc.engine.time.date() and svc.engine.backfilled


def test_sync_needs_saved_prices() -> None:
    svc = make_service(RealPrices(Store(":memory:"), KEYS))
    with pytest.raises(ValueError):
        asyncio.run(svc.sync_real_prices())


def test_new_markets_start_from_saved_real_prices() -> None:
    real = RealPrices(Store(":memory:"), KEYS)
    svc = make_service(real)
    real.record({"SPY": (772.31, date(2026, 10, 28))}, ["SPY"])
    asyncio.run(svc.goto(datetime(2020, 3, 2, 15, 0, tzinfo=UTC)))  # before the history: a new market
    assert svc.engine.quotes["SPY"].prev_close == 772.31
    assert svc.engine.quotes["XOM"].prev_close == next(s.price for s in svc.engine.specs if s.symbol == "XOM")


def test_refresh_fetches_every_symbol_at_once_and_reports_failures() -> None:
    calls = []

    def fetch(symbols: list[str], creds: Credentials) -> dict:
        calls.append(symbols)
        if len(calls) == 2:
            raise FetchError("couldn't reach Alpaca (timed out)")
        return {s: (100.0, date(2026, 10, 28)) for s in symbols}

    svc = make_service()
    clock = Clock()
    svc.real = RealPrices(svc.store, KEYS, fetch=fetch, now=clock)

    async def run() -> None:
        await asyncio.gather(svc.refresh_real_prices_if_stale(), svc.refresh_real_prices_if_stale())
        assert len(calls) == 1 and calls[0] == svc.symbols  # the second waited for the first
        assert svc.real_prices_status()["available"] == len(svc.symbols)
        clock.t += timedelta(minutes=1)
        await svc.refresh_real_prices_if_stale()
        status = svc.real_prices_status()
        assert status["last_error"] == "couldn't reach Alpaca (timed out)"
        assert status["available"] == len(svc.symbols)  # the saved prices stay

    asyncio.run(run())
