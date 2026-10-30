import asyncio
import json
import ssl
import urllib.error
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
    fetch_snapshots,
    tls_context,
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


def test_fetch_errors_read_as_messages(monkeypatch) -> None:
    import io
    import urllib.request

    def reset(*args, **kwargs):
        raise ConnectionResetError(54, "Connection reset by peer")

    monkeypatch.setattr(urllib.request, "urlopen", reset)
    with pytest.raises(FetchError, match="couldn't reach Alpaca"):
        fetch_snapshots(["SPY"], KEYS)
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: io.BytesIO(b"<html>Sign in to Wi-Fi</html>"))
    with pytest.raises(FetchError, match="wasn't readable"):
        fetch_snapshots(["SPY"], KEYS)

    def untrusted(*args, **kwargs):
        error = ssl.SSLCertVerificationError(1, "certificate verify failed")
        error.verify_message = "unable to get local issuer certificate"
        raise urllib.error.URLError(error)

    monkeypatch.setattr(urllib.request, "urlopen", untrusted)
    with pytest.raises(FetchError, match="doesn't trust the certificate.*unable to get local issuer"):
        fetch_snapshots(["SPY"], KEYS)


def test_requests_check_certificates_with_the_os_trust_store(monkeypatch) -> None:
    import io
    import urllib.request

    import truststore

    seen = {}

    def urlopen(request, timeout, context):
        seen["context"] = context
        return io.BytesIO(json.dumps(SNAPSHOTS).encode())

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    assert fetch_snapshots(["SPY"], KEYS)["SPY"][0] == 772.31
    assert seen["context"] is tls_context() and isinstance(seen["context"], truststore.SSLContext)


def test_schedule_and_old_alpha_vantage_keys() -> None:
    clock = Clock()
    store = Store(":memory:")
    store.set("real_prices_usage", {"day": "2026-10-28", "count": 3})
    real = RealPrices(store, KEYS, now=clock)
    assert store.get("real_prices_usage") is None  # cleaned up
    assert real.seconds_until_refresh() == 0  # never fetched
    real.attempt()
    real.record({"SPY": (1.0, date(2026, 10, 28))}, ["SPY"])
    assert real.seconds_until_refresh() == 15 * 60
    clock.t += timedelta(minutes=1)
    real.attempt()
    real.fetch_failed("couldn't reach Alpaca")
    assert real.seconds_until_refresh() == 60  # a failure retries in a minute
    assert real.status(["SPY"])["refresh_minutes"] == 15


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


def test_refresher_fetches_at_startup_then_on_schedule(monkeypatch) -> None:
    from paper_trade.market import service as service_mod

    monkeypatch.setattr(service_mod, "REAL_PRICES_CHECK_SECONDS", 0.001)
    clock = Clock()
    calls = []

    def fetch(symbols: list[str], creds: Credentials) -> dict:
        calls.append(clock.t)
        if len(calls) == 1:
            raise FetchError("couldn't reach Alpaca (offline)")
        return {s: (100.0, date(2026, 10, 28)) for s in symbols}

    svc = make_service()
    svc.real = RealPrices(svc.store, KEYS, fetch=fetch, now=clock)
    svc.real.attempt()  # a fetch just before a restart doesn't stop the one at startup

    async def run() -> None:
        async def settle() -> None:
            for _ in range(20):
                await asyncio.sleep(0.002)

        task = asyncio.create_task(svc._refresh_real_prices())
        await settle()
        assert len(calls) == 1  # at startup; it failed
        clock.t += timedelta(seconds=59)
        await settle()
        assert len(calls) == 1
        clock.t += timedelta(seconds=1)
        await settle()
        assert len(calls) == 2  # retried a minute later
        clock.t += timedelta(minutes=14)
        await settle()
        assert len(calls) == 2
        clock.t += timedelta(minutes=1)
        await settle()
        assert len(calls) == 3  # then every 15 minutes
        task.cancel()

    asyncio.run(run())


def test_status_fetches_when_over_a_minute_old_and_waits_at_most_a_moment(monkeypatch) -> None:
    import time

    from paper_trade.api import market as api_mod

    clock = Clock()
    calls = []
    slow = False

    def fetch(symbols: list[str], creds: Credentials) -> dict:
        calls.append(clock.t)
        if slow:
            time.sleep(0.3)
        return {s: (100.0, date(2026, 10, 28)) for s in symbols}

    monkeypatch.setattr(api_mod, "STATUS_FETCH_WAIT_SECONDS", 0.05)
    svc = make_service()
    svc.real = RealPrices(svc.store, KEYS, fetch=fetch, now=clock)

    class Request:
        class app:
            class state:
                market = svc

    async def run() -> None:
        assert (await api_mod.real_prices(Request))["available"] == 40
        await api_mod.real_prices(Request)
        assert len(calls) == 1  # fresh: not fetched again
        clock.t += timedelta(minutes=1)
        nonlocal slow
        slow = True
        started = time.monotonic()
        await api_mod.real_prices(Request)
        assert time.monotonic() - started < 0.25 and len(calls) == 2  # answered before the fetch finished
        await asyncio.sleep(0.4)
        assert svc.real.saved["SPY"].fetched == clock.t  # the fetch finished in the background

    asyncio.run(run())
