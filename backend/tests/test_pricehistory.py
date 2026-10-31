import asyncio
from datetime import UTC, date, datetime

import pytest

from paper_trade.market.clock import SimClock
from paper_trade.market.engine import MarketEngine
from paper_trade.market.service import MarketService
from paper_trade.market.symbols import SYMBOLS
from paper_trade.pricehistory import PriceHistory, back_cast, parse_closes
from paper_trade.realprices import Credentials, FetchError, FetchStop, RealPrices
from paper_trade.store import Store

START = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)
SPY = [(date(2006, 1, 3), 126.7), (date(2006, 1, 4), 127.3), (date(2006, 1, 5), 127.38)]


def history(closes=None, key="k") -> PriceHistory:
    hist = PriceHistory(Store(":memory:"), key, fetch=lambda symbol, key: closes[symbol])
    for symbol, rows in (closes or {}).items():
        hist.record(symbol, rows)
    return hist


def test_parse_closes() -> None:
    body = [{"date": "2006-01-03T00:00:00.000Z", "close": 126.699997}, {"date": "2006-01-04T00:00:00.000Z", "close": 0}]
    assert parse_closes(body) == [(date(2006, 1, 3), 126.699997)]
    with pytest.raises(FetchError):
        parse_closes({"detail": "Error"})


def test_a_close_on_or_before_a_day() -> None:
    hist = history({"SPY": SPY})
    assert hist.price_on("SPY", date(2006, 1, 4), 0.08) == 127.3
    assert hist.price_on("SPY", date(2006, 1, 8), 0.08) == 127.38  # a weekend: the last close
    assert hist.price_on("QQQ", date(2006, 1, 4), 0.08) is None


def test_before_the_first_close_it_is_brought_back_by_the_drift() -> None:
    hist = history({"SPY": SPY})
    price = hist.price_on("SPY", date(2005, 1, 3), 0.10)
    assert price == pytest.approx(126.7 * 0.9048, rel=1e-3)
    assert back_cast(100, date(2026, 1, 1), date(2016, 1, 1), 0.07) == pytest.approx(100 / 2.0137, rel=1e-3)


def test_due_symbols_and_a_pause_after_a_failure() -> None:
    hist = history({"SPY": SPY})
    assert hist.due(["SPY", "QQQ"]) == ["QQQ"]
    hist.failed("too many requests")
    assert hist.due(["SPY", "QQQ"]) == []
    assert hist.status(["SPY", "QQQ"])["last_error"] == "too many requests"
    hist._now = lambda: datetime(2100, 1, 1, tzinfo=UTC)
    assert hist.due(["SPY", "QQQ"]) == ["QQQ"]


def test_history_is_kept_between_runs(tmp_path) -> None:
    path = tmp_path / "t.db"
    PriceHistory(Store(path), "k").record("SPY", SPY)
    again = PriceHistory(Store(path), "k")
    assert again.fetched == {"SPY"}
    assert again.first_close("SPY") == SPY[0]


def make_service(hist: PriceHistory | None, real: RealPrices | None = None) -> MarketService:
    return MarketService(SimClock(start=START), MarketEngine(START), Store(":memory:"), history_days=5, real=real, price_history=hist)


def test_a_market_started_in_the_past_opens_at_the_real_price_of_then() -> None:
    svc = make_service(history({"SPY": SPY}))
    prices = dict(zip(svc.symbols, svc.real_start_prices(date(2006, 1, 4)), strict=True))
    assert prices["SPY"] == 126.7  # the close before the 4th
    spec = {s.symbol: s for s in SYMBOLS}
    assert prices["QQQ"] == spec["QQQ"].price  # nothing saved for it


def test_without_closes_today_s_real_price_is_brought_back() -> None:
    real = RealPrices(Store(":memory:"), Credentials("i", "s"))
    real.saved["SPY"] = type("P", (), {"price": 700.0, "as_of": date(2026, 1, 2)})()
    svc = make_service(None, real)
    prices = dict(zip(svc.symbols, svc.real_start_prices(date(2016, 1, 5)), strict=True))
    assert 150 < prices["SPY"] < 450
    assert svc.real_start_prices(None)[svc.symbols.index("SPY")] == 700.0


def test_nothing_real_at_all_keeps_the_specs_prices() -> None:
    assert make_service(history()).real_start_prices(date(2006, 1, 4)) is None


def test_the_background_fetch_saves_each_symbol_and_stops_on_a_failure(monkeypatch) -> None:
    monkeypatch.setattr("paper_trade.market.service.PRICE_HISTORY_PAUSE_SECONDS", 0)
    calls = []

    def fetch(symbol, key):
        calls.append(symbol)
        if symbol == "QQQ":
            raise FetchStop("Tiingo says too many requests")
        return SPY

    hist = PriceHistory(Store(":memory:"), "k", fetch=fetch)
    svc = make_service(hist)

    async def run() -> None:
        task = asyncio.create_task(svc._refresh_price_history())
        await asyncio.sleep(0.2)
        task.cancel()

    asyncio.run(run())
    assert calls == ["SPY", "QQQ"]
    assert hist.fetched == {"SPY"}
    assert "too many" in hist.status(svc.symbols)["last_error"]


LATER = [(date(2026, 1, 6), 100.0), (date(2026, 1, 7), 101.0)]  # AAPL's first close is after START's day


def test_a_symbol_is_hidden_until_its_first_real_close() -> None:
    svc = make_service(history({"AAPL": LATER, "SPY": SPY}))
    assert svc.listed_from("AAPL") == date(2026, 1, 6) and svc.listed_from("QQQ") is None
    assert not svc.is_listed("AAPL") and svc.is_listed("AAPL", date(2026, 1, 6))
    assert svc.is_listed("QQQ")  # nothing saved: always listed
    symbols = {q["symbol"] for q in svc.listed_quotes()}
    assert "AAPL" not in symbols and {"SPY", "QQQ"} <= symbols
    assert "AAPL" not in {q["symbol"] for q in svc.snapshot()["quotes"]}


def test_an_unlisted_symbol_can_t_be_traded_or_have_a_chain() -> None:
    svc = make_service(history({"AAPL": LATER}))
    with pytest.raises(Exception, match="wasn't trading yet"):
        svc.broker.place("AAPL", "buy", 1, "market")
    with pytest.raises(Exception, match="wasn't trading yet"):
        svc.broker.place("AAPL270115C00100000", "buy", 1, "market")
    with pytest.raises(ValueError, match="wasn't trading yet"):
        svc.option_chain("AAPL", date(2026, 1, 16))
    svc.broker.place("QQQ", "buy", 1, "market")


def test_charts_have_no_bars_from_before_the_listing() -> None:
    svc = make_service(history({"SPY": SPY}))
    start = int(datetime(2006, 1, 3, 14, 30, tzinfo=UTC).timestamp())
    svc.history.bars = lambda *a, **k: {"bars": [[start - 86400, 1, 1, 1, 1, 1], [start, 1, 1, 1, 1, 1]], "more": True}
    out = svc.bars("SPY", "1D", None, 10)
    assert [b[0] for b in out["bars"]] == [start] and out["more"] is False


def test_a_symbol_tiingo_doesn_t_know_is_skipped_but_a_rate_limit_stops(monkeypatch) -> None:
    def fetch(symbol, key):
        if symbol == "SPY":
            raise FetchError("Tiingo answered 404 Not found")
        if symbol == "IWM":
            raise FetchStop("Tiingo says too many requests")
        return SPY

    hist = PriceHistory(Store(":memory:"), "k", fetch=fetch)
    svc = make_service(hist)
    monkeypatch.setattr("paper_trade.market.service.PRICE_HISTORY_PAUSE_SECONDS", 0)
    monkeypatch.setattr("paper_trade.market.service.PRICE_HISTORY_CHECK_SECONDS", 3600)

    async def run() -> None:
        task = asyncio.create_task(svc._refresh_price_history())
        await asyncio.sleep(0.2)
        task.cancel()

    asyncio.run(run())
    assert "QQQ" in hist.fetched and "SPY" not in hist.fetched and "SPY" not in hist.due(svc.symbols)
    assert "IWM" not in hist.fetched and hist.due(svc.symbols) == []  # stopped at the rate limit: wait an hour


def test_a_newer_saved_real_price_beats_an_older_close() -> None:
    svc = make_service(history({"SPY": SPY}), real=RealPrices(Store(":memory:"), Credentials("a", "b"), fetch=lambda s, c: {}))
    svc.real.saved["SPY"] = type("P", (), {"price": 700.0, "as_of": date(2026, 1, 2)})()
    prices = dict(zip(svc.symbols, svc.real_start_prices(date(2026, 1, 5)), strict=True))
    assert prices["SPY"] == 700.0
