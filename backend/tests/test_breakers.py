import math
from datetime import datetime, timedelta

import numpy as np
import pytest

from paper_trade.market import engine as engine_mod
from paper_trade.market.calendar import MARKET_TZ, market_day, session_open
from paper_trade.market.engine import MarketEngine
from paper_trade.store import Store
from paper_trade.trading.broker import Broker, OrderError

START = datetime(2026, 1, 5, 9, 30, tzinfo=MARKET_TZ)
M = engine_mod._M


def lows(prev: float, drops: dict[int, float]) -> np.ndarray:
    """SPY's minute lows (log): flat at the previous close except at the given minutes (fall from the close)."""
    out = np.full(M, math.log(prev))
    for m, fall in drops.items():
        out[m] = math.log(prev * (1 - fall))
    return out


def test_levels_halt_at_7_and_13_percent_for_15_minutes_and_20_percent_for_the_day() -> None:
    halts = MarketEngine._halts(math.log(100.0), lows(100.0, {30: 0.075}))
    assert halts == [(31, 46, 1)]  # the next minute on, 15 minutes
    both = MarketEngine._halts(math.log(100.0), lows(100.0, {30: 0.075, 60: 0.135}))
    assert both == [(31, 46, 1), (61, 76, 2)]
    # at reopening the price is already past 13%: the second halt follows the first
    deep = MarketEngine._halts(math.log(100.0), lows(100.0, {30: 0.075, 46: 0.14}))
    assert deep == [(31, 46, 1), (47, 62, 2)]
    full = MarketEngine._halts(math.log(100.0), lows(100.0, {30: 0.075, 60: 0.135, 90: 0.205}))
    assert full[-1] == (91, M, 3)


def test_late_in_the_session_only_the_last_level_halts() -> None:
    assert MarketEngine._halts(math.log(100.0), lows(100.0, {360: 0.08})) == []
    assert MarketEngine._halts(math.log(100.0), lows(100.0, {360: 0.14})) == []
    assert MarketEngine._halts(math.log(100.0), lows(100.0, {360: 0.21})) == [(361, M, 3)]


def test_no_halt_on_an_ordinary_day() -> None:
    assert MarketEngine._halts(math.log(100.0), lows(100.0, {100: 0.03})) == []


def find_halt(levels: int = 1):
    """A market whose triggered shock halts trading (searched over seeds: deterministic)."""
    for seed in range(1, 200):
        engine = MarketEngine(START, seed=seed)
        engine.advance(START + timedelta(days=10))
        day = engine.trigger_shock()
        engine.advance(session_open(day))
        sk = engine._skeleton(day)
        if sk.halts and sk.halts[0][1] < M:
            return engine, day, sk
    raise AssertionError("no seed halted")


def test_a_halt_freezes_prices_and_volume_and_the_market_reopens() -> None:
    engine, day, sk = find_halt()
    first, resume, level = sk.halts[0]
    assert level == 1 and sk.halted[first:resume].all() and not sk.halted[:first].any()
    assert (sk.volume[first:resume] == 0).all()
    for part in (sk.open, sk.high, sk.low, sk.close):
        assert np.allclose(part[first:resume], sk.close[first - 1])  # every symbol stands still
    engine.advance(session_open(day) + timedelta(minutes=first, seconds=30))
    halt = engine.halt
    assert halt is not None and halt["level"] == 1
    assert halt["resumes"] == (session_open(day) + timedelta(minutes=resume)).isoformat()
    engine.advance(session_open(day) + timedelta(minutes=resume, seconds=5))
    assert engine.halt is None or engine.halt["level"] > 1  # reopened (a deeper level may follow)


def test_orders_are_refused_during_a_halt() -> None:
    engine, day, sk = find_halt()
    first, resume, _ = sk.halts[0]
    engine.advance(session_open(day) + timedelta(minutes=first, seconds=30))
    broker = Broker(engine, Store(":memory:"))
    with pytest.raises(OrderError, match="halted until"):
        broker.place("SPY", "buy", 1, "market")
    engine.advance(session_open(day) + timedelta(minutes=resume, seconds=5))
    if engine.halt is None:
        broker.place("SPY", "buy", 1, "market")


def test_a_resting_order_cannot_fill_in_a_halt() -> None:
    engine, day, sk = find_halt()
    first, resume, _ = sk.halts[0]
    col = engine.symbol_index("SPY")
    high, low = engine.minute_range(day, col)
    price = float(np.exp(sk.close[first - 1, col]))
    hit = engine.first_hit(day, col, 60 * first, 60 * resume - 1, np.ones(M, dtype=bool), lambda p, s: p > 0)
    assert hit is None  # nothing trades in the halted minutes, even at the frozen price
    assert price > 0 and low.shape == (M,)


def test_a_shock_day_is_less_extreme_in_a_crisis() -> None:
    assert engine_mod.SHOCK_IN_CRISIS < 1
    engine = MarketEngine(START, seed=3)
    engine._shocks[START.date()] = (0.6, START)
    sk = engine._skeleton(START.date())
    assert sk.scale == pytest.approx(1 + 0.6 * engine_mod.SHOCK_IN_CRISIS) and market_day(START) == START.date()
