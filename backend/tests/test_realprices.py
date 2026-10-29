import asyncio
from datetime import UTC, date, datetime, timedelta

import numpy as np
import pytest

from paper_trade.market.clock import SimClock
from paper_trade.market.engine import MarketEngine
from paper_trade.market.service import MarketService
from paper_trade.realprices import (
    DAILY_REQUESTS,
    DailyLimit,
    RealPrices,
    Throttled,
    api_key,
    parse_quote,
)
from paper_trade.store import Store

START = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)
QUOTE = {
    "Global Quote": {
        "01. symbol": "SPY",
        "05. price": "772.3100",
        "07. latest trading day": "2026-10-28",
    }
}


class Clock:
    def __init__(self) -> None:
        self.t = datetime(2026, 10, 29, 14, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.t


def test_parse_quote() -> None:
    assert parse_quote(QUOTE) == (772.31, date(2026, 10, 28))
    with pytest.raises(DailyLimit):
        parse_quote({"Information": "Our standard API rate limit is 25 requests per day."})
    with pytest.raises(Throttled):
        parse_quote({"Note": "Please consider spreading out your free API requests more sparingly (1 request per second)."})
    with pytest.raises(ValueError, match="apikey is invalid"):  # a problem to show, not a limit to wait out
        parse_quote({"Information": "The parameter apikey is invalid or missing."})
    with pytest.raises(ValueError):
        parse_quote({"Error Message": "Invalid API call."})


def test_api_key_from_env_file(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("ALPHAVANTAGE_API_KEY", raising=False)
    env = tmp_path / ".env"
    assert api_key(env) is None
    env.write_text("# local\nALPHAVANTAGE_API_KEY = abc123\n")
    assert api_key(env) == "abc123"
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", "fromenv")
    assert api_key(env) == "fromenv"


def test_never_fetched_then_stalest_first_within_the_daily_budget() -> None:
    clock = Clock()
    real = RealPrices(Store(":memory:"), "key", fetch=lambda s, k: (1.0, date(2026, 10, 28)), now=clock)
    symbols = ["A", "B", "C"]
    real.record("B", 2.0, date(2026, 10, 27))
    clock.t += timedelta(hours=1)
    real.record("C", 3.0, date(2026, 10, 27))
    clock.t += timedelta(hours=12)
    assert real.due(symbols) == ["A", "B", "C"]
    clock.t -= timedelta(hours=1)  # C was fetched under 12 hours ago
    assert real.due(symbols) == ["A", "B"]

    for _ in range(DAILY_REQUESTS):
        real.attempt("X")
    assert real.requests_left() == 0 and real.next_due(symbols) is None
    clock.t += timedelta(days=1)  # a new day, a new budget
    assert real.requests_left() == DAILY_REQUESTS and real.next_due(symbols) == "A"


def test_a_failing_symbol_waits_and_refusals_pause() -> None:
    clock = Clock()
    real = RealPrices(Store(":memory:"), "key", now=clock)
    real.attempt("A")
    real.failed("A: timed out")
    assert real.next_due(["A", "B"]) == "B"  # A waits an hour instead of taking the budget
    assert real.status(["A", "B"])["last_error"] == "A: timed out"
    clock.t += timedelta(hours=1)
    assert real.next_due(["A", "B"]) == "A"

    real.refused(Throttled("1 request per second"))
    assert real.next_due(["A"]) is None and real.seconds_until_retry(1800) == 60
    clock.t += timedelta(minutes=1)
    assert real.next_due(["A"]) == "A"
    real.refused(DailyLimit("25 requests per day"))
    assert real.seconds_until_retry(1800) == 1800  # paused for the hour
    clock.t += timedelta(hours=1)
    assert real.next_due(["A"]) == "A"
    real.record("A", 1.0, date(2026, 10, 28))
    assert real.status(["A"])["last_error"] is None


def test_nothing_is_fetched_without_a_key() -> None:
    real = RealPrices(Store(":memory:"), None)
    assert real.next_due(["SPY"]) is None
    assert real.status(["SPY"])["configured"] is False


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
    real = RealPrices(Store(":memory:"), "key")
    svc = make_service(real)
    real.store = svc.store
    asyncio.run(svc.build_history())
    order = svc.broker.place("AAPL", "buy", 1, "market")
    others = {s: q.last for s, q in svc.engine.quotes.items()}
    real.record("SPY", 772.31, date(2026, 10, 28))
    real.record("AAPL", 270.0, date(2026, 10, 28))

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
    svc = make_service(RealPrices(Store(":memory:"), "key"))
    with pytest.raises(ValueError):
        asyncio.run(svc.sync_real_prices())


def test_new_markets_start_from_saved_real_prices() -> None:
    real = RealPrices(Store(":memory:"), "key")
    svc = make_service(real)
    real.record("SPY", 772.31, date(2026, 10, 28))
    asyncio.run(svc.goto(datetime(2020, 3, 2, 15, 0, tzinfo=UTC)))  # before the history: a new market
    assert svc.engine.quotes["SPY"].prev_close == 772.31
    assert svc.engine.quotes["XOM"].prev_close == next(s.price for s in svc.engine.specs if s.symbol == "XOM")


def test_refresher_records_prices_and_stops_for_the_day_when_refused(monkeypatch) -> None:
    from paper_trade.market import service as service_mod

    monkeypatch.setattr(service_mod, "REQUEST_SPACING_SECONDS", 0)
    monkeypatch.setattr(service_mod, "REAL_PRICES_IDLE_SECONDS", 0)
    calls = []

    def fetch(symbol: str, key: str) -> tuple[float, date]:
        calls.append(symbol)
        if len(calls) == 3:
            raise DailyLimit("25 requests per day")
        return 100.0 + len(calls), date(2026, 10, 28)

    svc = make_service()
    svc.real = RealPrices(svc.store, "key", fetch=fetch)

    async def run() -> None:
        task = asyncio.create_task(svc._refresh_real_prices())
        for _ in range(50):
            await asyncio.sleep(0.01)
        task.cancel()

    asyncio.run(run())
    assert len(calls) == 3  # refused on the third: paused
    assert set(svc.real.saved) == set(calls[:2])
    assert svc.real.status(svc.symbols)["last_error"] == "25 requests per day"
