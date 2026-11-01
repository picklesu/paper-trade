import asyncio
import json
import math
import ssl
import urllib.error
from datetime import UTC, date, datetime, timedelta

import numpy as np
import pytest

from paper_trade import update
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

    class Response(io.BytesIO):
        headers: dict[str, str] = {}

    def urlopen(request, timeout, context):
        seen["context"] = context
        return Response(json.dumps(SNAPSHOTS).encode())

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    assert fetch_snapshots(["SPY"], KEYS)["SPY"][0] == 772.31
    assert seen["context"] is tls_context() and isinstance(seen["context"], truststore.SSLContext)


def test_old_alpha_vantage_keys_are_cleaned_up() -> None:
    store = Store(":memory:")
    store.set("real_prices_usage", {"day": "2026-10-28", "count": 3})
    RealPrices(store, KEYS)
    assert store.get("real_prices_usage") is None


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
    real.record({"SPY": (772.31, date(2026, 10, 28))}, ["SPY", "XOM"])
    assert real.status(["SPY", "XOM"])["last_error"] == "no price for XOM"
    clock.t += timedelta(minutes=1)

    real.record({}, ["SPY"])
    assert real.status(["SPY"])["last_error"] == "Alpaca sent no prices"
    real.record({"SPY": (780.0, date(2026, 10, 29))}, ["SPY"])
    status = real.status(["SPY", "XOM"])
    assert status["last_error"] is None and status["available"] == 1 and status["newest"] == "2026-10-29"
    assert status["fetched"] == clock.t.isoformat()
    # Saved prices survive a restart.
    assert RealPrices(store, KEYS).saved["SPY"].price == 780.0


def make_service(real: RealPrices | None = None) -> MarketService:
    clock = SimClock(start=START, real_time=lambda: 0.0)
    return MarketService(clock, MarketEngine(START), Store(":memory:"), history_days=5, real=real)


def test_new_markets_start_from_saved_real_prices() -> None:
    real = RealPrices(Store(":memory:"), KEYS)
    svc = make_service(real)
    real.record({"SPY": (772.31, date(2026, 10, 28))}, ["SPY"])
    asyncio.run(svc.goto(datetime(2020, 3, 2, 15, 0, tzinfo=UTC)))  # before the history: a new market
    # Today's real price, brought back to the day before by SPY's expected return.
    spy = next(s for s in svc.engine.specs if s.symbol == "SPY")
    years = (date(2026, 10, 28) - date(2020, 2, 28)).days / 365.25
    assert svc.engine.quotes["SPY"].prev_close == pytest.approx(772.31 * math.exp(-spy.drift * years))
    assert svc.engine.quotes["XOM"].prev_close == next(s.price for s in svc.engine.specs if s.symbol == "XOM")


def test_update_fetches_every_symbol_at_once_and_reports_failures() -> None:
    calls = []

    def fetch(symbols: list[str], creds: Credentials) -> dict:
        calls.append(symbols)
        if len(calls) == 2:
            raise FetchError("couldn't reach Alpaca (timed out)")
        return {s: (100.0, date(2026, 10, 28)) for s in symbols}

    names = [s.symbol for s in make_service().engine.specs]
    real = RealPrices(Store(":memory:"), KEYS, fetch=fetch, now=Clock())
    assert update.update_prices(real, names, say=lambda m: None) == []
    assert len(calls) == 1 and calls[0] == names  # one request for all of them
    assert real.status(names)["available"] == len(names)
    problems = update.update_prices(real, names, say=lambda m: None)
    assert problems == ["real prices: couldn't reach Alpaca (timed out)"]
    status = real.status(names)
    assert status["last_error"] == "couldn't reach Alpaca (timed out)" and status["available"] == len(names)  # the saved prices stay


def test_update_without_keys_says_what_to_add() -> None:
    problems = update.update_prices(RealPrices(Store(":memory:"), None), ["SPY"], say=lambda m: None)
    assert "APCA_API_KEY_ID" in problems[0]


def test_the_app_makes_no_requests_and_has_no_real_data_endpoints(monkeypatch) -> None:
    from paper_trade.market.service import MarketService as Service

    calls = []
    real = RealPrices(Store(":memory:"), KEYS, fetch=lambda symbols, creds: calls.append(1) or {})
    svc = Service(SimClock(start=START, real_time=lambda: 0.0), MarketEngine(START), real.store, 5, real)

    async def run() -> None:
        svc.start()
        await asyncio.sleep(0.05)
        await svc.stop()

    asyncio.run(run())
    assert calls == []


def test_the_update_command_reports_missing_keys_and_exits_nonzero(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("PAPER_TRADE_DB", str(tmp_path / "t.db"))
    assert update.main([]) == 1  # the tests' fixture hides every key
    out = capsys.readouterr().out
    for name in ("APCA_API_KEY_ID", "TIINGO_API_KEY", "FRED_API_KEY"):
        assert name in out
    assert update.main(["prices"]) == 1
