import asyncio
from datetime import UTC, date, datetime

import pytest

from paper_trade.market.clock import SimClock
from paper_trade.market.engine import MarketEngine
from paper_trade.market.service import MarketService
from paper_trade.market.symbols import SYMBOLS
from paper_trade.pricehistory import Bar, PriceHistory, back_cast, parse_bars
from paper_trade.realprices import Credentials, FetchError, FetchStop, RealPrices
from paper_trade.store import Store

START = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)


def bars(rows: list[tuple[date, float]]) -> list[Bar]:
    return [Bar(day, close, close, close, close, 1000.0) for day, close in rows]


SPY = bars([(date(2006, 1, 3), 126.7), (date(2006, 1, 4), 127.3), (date(2006, 1, 5), 127.38)])


def history(closes=None, key="k") -> PriceHistory:
    hist = PriceHistory(Store(":memory:"), key, fetch=lambda symbol, key: closes[symbol])
    for symbol, rows in (closes or {}).items():
        hist.record(symbol, rows)
    return hist


def test_parse_bars_skips_rows_without_a_close() -> None:
    body = [{"date": "2006-01-03T00:00:00.000Z", "close": 126.699997, "open": 126, "high": 127, "low": 125, "volume": 5}, {"date": "2006-01-04T00:00:00.000Z", "close": 0}]
    assert parse_bars(body) == [Bar(date(2006, 1, 3), 126.0, 127.0, 125.0, 126.699997, 5.0)]
    with pytest.raises(FetchError):
        parse_bars({"detail": "Error"})


def test_bars_are_adjusted_for_later_splits() -> None:
    raw = [
        {"date": "2020-08-28", "open": 500, "high": 510, "low": 490, "close": 499.0, "volume": 100, "divCash": 0.82, "splitFactor": 1},
        {"date": "2020-08-31", "open": 127, "high": 131, "low": 126, "close": 129.0, "volume": 400, "divCash": 0, "splitFactor": 4},
        {"date": "2020-09-01", "open": 130, "high": 134, "low": 128, "close": 134.0, "volume": 300, "divCash": 0, "splitFactor": 1},
    ]
    got = parse_bars(raw[::-1])  # any order in
    assert got[0] == Bar(date(2020, 8, 28), 125.0, 127.5, 122.5, 124.75, 400.0, 0.205, 1.0)  # a quarter of the price, four times the shares
    assert got[1].close == 129.0 and got[1].split == 4.0 and got[2].close == 134.0  # the split day and after are as traded


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
    assert again.first_close("SPY") == (SPY[0].day, SPY[0].close)


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


LATER = bars([(date(2026, 1, 6), 100.0), (date(2026, 1, 7), 101.0)])  # AAPL's first close is after START's day


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


def test_a_reset_waits_until_some_history_is_downloaded() -> None:
    svc = make_service(PriceHistory(Store(":memory:"), "k", fetch=lambda s, k: SPY))
    past = datetime(2006, 1, 3, 15, 0, tzinfo=UTC)
    with pytest.raises(ValueError, match="still downloading"):
        asyncio.run(svc.goto(past))
    svc.price_history.record("SPY", SPY)
    assert asyncio.run(svc.goto(past))[0] == "reset"


def test_bars_and_the_last_day_come_back_from_the_store() -> None:
    hist = history({"SPY": SPY})
    assert [b.day for b in hist.bars("SPY", date(2006, 1, 4))] == [date(2006, 1, 4), date(2006, 1, 5)]
    assert hist.bars("SPY", end=date(2006, 1, 3))[0].close == 126.7 and hist.bars("QQQ") == []
    assert hist.last_day("SPY") == date(2006, 1, 5) and hist.last_day("QQQ") is None


def test_the_old_closes_table_is_dropped() -> None:
    store = Store(":memory:")
    store.connection.execute("CREATE TABLE closes (symbol TEXT, day TEXT, close REAL)")
    PriceHistory(store, "k")
    assert not store.connection.execute("SELECT name FROM sqlite_master WHERE name = 'closes'").fetchone()


def test_update_fetches_only_the_days_since_the_last_bar(monkeypatch) -> None:
    monkeypatch.setattr("paper_trade.market.service.PRICE_HISTORY_PAUSE_SECONDS", 0)
    asked = []

    def since(symbol, key, day):
        asked.append((symbol, day))
        return bars([(date(2006, 1, 5), 127.4), (date(2006, 1, 6), 128.0)])  # the last day again, then a new one

    hist = PriceHistory(Store(":memory:"), "k", fetch_since=since)
    hist.record("SPY", SPY)
    svc = make_service(hist)
    result = asyncio.run(svc.update_real_history())
    assert asked == [("SPY", date(2006, 1, 5))] and result["prices"] == {"SPY": 1}
    assert [b.close for b in hist.bars("SPY")] == [126.7, 127.3, 127.4, 128.0]  # the last day was replaced
    assert hist.newest_day() == "2006-01-06"
    assert asyncio.run(svc.update_real_history())["prices"] == {}  # nothing new the second time


def test_a_split_among_the_new_days_fetches_the_whole_history_again(monkeypatch) -> None:
    monkeypatch.setattr("paper_trade.market.service.PRICE_HISTORY_PAUSE_SECONDS", 0)
    whole = [Bar(date(2006, 1, 3), 63.35, 63.35, 63.35, 63.35, 2000.0), Bar(date(2006, 1, 4), 63.65, 63.65, 63.65, 63.65, 2000.0),
             Bar(date(2006, 1, 5), 63.69, 63.69, 63.69, 63.69, 2000.0), Bar(date(2006, 1, 6), 64.0, 64.0, 64.0, 64.0, 1000.0, 0.0, 2.0)]
    hist = PriceHistory(Store(":memory:"), "k", fetch=lambda s, k: whole, fetch_since=lambda s, k, d: [whole[3]])
    hist.record("SPY", SPY)
    result = asyncio.run(make_service(hist).update_real_history())
    assert result["prices"] == {"SPY": 1}
    assert [b.close for b in hist.bars("SPY")] == [63.35, 63.65, 63.69, 64.0]  # earlier days re-adjusted


def test_update_stops_on_a_refusal_and_names_it(monkeypatch) -> None:
    monkeypatch.setattr("paper_trade.market.service.PRICE_HISTORY_PAUSE_SECONDS", 0)

    def since(symbol, key, day):
        raise FetchStop("Tiingo says too many requests")

    hist = PriceHistory(Store(":memory:"), "k", fetch_since=since)
    hist.record("SPY", SPY)
    hist.record("QQQ", SPY)
    result = asyncio.run(make_service(hist).update_real_history())
    assert result["problems"] == ["Tiingo says too many requests"] and result["prices"] == {}
