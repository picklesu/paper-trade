import asyncio
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from paper_trade.market.clock import SimClock
from paper_trade.market.engine import MarketEngine
from paper_trade.market.service import MarketService
from paper_trade.store import Store
from paper_trade.watchlists import Watchlists

START = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)
SYMBOLS = ["SPY", "QQQ", "AAPL", "XOM"]


def test_defaults_then_saved() -> None:
    store = Store(":memory:")
    lists = Watchlists(store, SYMBOLS)
    assert [(w["name"], w["symbols"]) for w in lists.lists] == [
        ("Main", ["SPY", "QQQ", "AAPL"]),  # the defaults, limited to known symbols
        ("All symbols", SYMBOLS),
    ]
    lists.update(1, symbols=["xom", "SPY"])
    assert Watchlists(store, SYMBOLS).get(1)["symbols"] == ["XOM", "SPY"]  # saved


def test_create_rename_reorder_delete() -> None:
    lists = Watchlists(Store(":memory:"), SYMBOLS)
    tech = lists.create("  Tech   names ", ["AAPL", "QQQ", "AAPL"])
    assert tech == {"id": 3, "name": "Tech names", "symbols": ["AAPL", "QQQ"]}  # spaces tidied, no duplicates
    lists.update(3, symbols=["QQQ", "AAPL"])
    lists.update(3, name="Tech")
    assert lists.get(3) == {"id": 3, "name": "Tech", "symbols": ["QQQ", "AAPL"]}
    lists.update(3, name="tech")  # its own name in another case is fine
    lists.delete(3)
    assert lists.create("Next")["id"] == 4  # ids aren't reused


@pytest.mark.parametrize(
    "call, message",
    [
        (lambda w: w.create(""), "needs a name"),
        (lambda w: w.create("x" * 41), "at most 40"),
        (lambda w: w.create("main"), "already a watchlist"),
        (lambda w: w.create("New", ["ZZZZ"]), "unknown symbol"),
        (lambda w: w.update(1, name="All Symbols"), "already a watchlist"),
    ],
)
def test_invalid_changes_are_refused(call, message) -> None:
    lists = Watchlists(Store(":memory:"), SYMBOLS)
    with pytest.raises(ValueError, match=message):
        call(lists)
    assert [w["name"] for w in lists.lists] == ["Main", "All symbols"]  # unchanged


def test_a_failed_update_changes_nothing() -> None:
    lists = Watchlists(Store(":memory:"), SYMBOLS)
    with pytest.raises(ValueError):
        lists.update(1, name="Renamed", symbols=["ZZZZ"])
    assert lists.get(1)["name"] == "Main"


def test_the_last_list_stays() -> None:
    lists = Watchlists(Store(":memory:"), SYMBOLS)
    lists.delete(2)
    with pytest.raises(ValueError, match="last watchlist"):
        lists.delete(1)
    with pytest.raises(KeyError):
        lists.delete(9)


def make_service() -> MarketService:
    clock = SimClock(start=START, real_time=lambda: 0.0)
    return MarketService(clock, MarketEngine(START), Store(":memory:"), history_days=30)


def test_quote_stats_cover_finished_sessions() -> None:
    svc = make_service()
    asyncio.run(svc.build_history())
    stats = svc.quote_stats()
    bars = {s: svc.history.bars(s, "1D", count=300)["bars"][:-1] for s in ("SPY", "TLT")}  # not today
    for symbol, daily in bars.items():
        assert stats[symbol]["high_52w"] == pytest.approx(max(b[2] for b in daily), abs=0.01)
        assert stats[symbol]["low_52w"] == pytest.approx(min(b[3] for b in daily), abs=0.01)
        assert stats[symbol]["avg_volume"] == pytest.approx(np.mean([b[5] for b in daily]), rel=0.01)
    assert svc.quote_stats() is stats  # cached
    svc.timeline += 1
    assert svc.quote_stats() is not stats  # history changed


def test_sparklines_follow_the_day() -> None:
    svc = make_service()
    svc.engine.advance(START + timedelta(minutes=17, seconds=30))  # 10:17:30
    data = svc.sparklines(["SPY", "nope"])
    spy = data["series"]["SPY"]
    assert data["step_minutes"] == 5 and set(data["series"]) == {"SPY"}
    assert len(spy) == 10  # 9:34, 9:39, ... 10:14, then now
    assert spy[-1] == round(svc.engine.quotes["SPY"].last, 2)
    assert spy[0] == round(float(np.exp(svc.engine._skeleton(START.date()).close[4, 0])), 2)


def test_quote_sizes_are_round_lots_and_steady_within_a_second() -> None:
    engine = MarketEngine(START)
    engine.advance(START + timedelta(minutes=3, seconds=10.2))
    q = engine.quotes
    sizes = {s: (x.bid_size, x.ask_size) for s, x in q.items()}
    assert all(b % 100 == 0 and a % 100 == 0 and b >= 100 and a >= 100 for b, a in sizes.values())
    engine.advance(START + timedelta(minutes=3, seconds=10.7))
    assert {s: (x.bid_size, x.ask_size) for s, x in engine.quotes.items()} == sizes
    # Heavily traded symbols show more on average.
    samples = []
    for k in range(200):
        engine.advance(START + timedelta(minutes=4, seconds=k))
        samples.append((engine.quotes["SPY"].bid_size, engine.quotes["XLV"].bid_size))
    spy, xlv = np.mean(samples, axis=0)
    assert spy > 2 * xlv
