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


def find_halt():
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
    hit = engine.first_hit(day, col, 60 * first, 60 * resume - 1, np.ones(M, dtype=bool), lambda p, s: p > 0)
    assert hit is None  # nothing trades in the halted minutes, even at the frozen price
    assert engine.next_trading_second(day, 60 * first + 5) == 60 * resume
    assert engine.reopens_at(day, 60 * resume) and not engine.reopens_at(day, 60 * resume + 1)
    # a limit order that rests through the halt fills at the reopening, not during it
    broker = Broker(engine, Store(":memory:"))
    engine.advance(session_open(day) + timedelta(minutes=first - 5))
    price = broker.engine.quotes["SPY"].last
    order = broker.place("SPY", "buy", 1, "limit", limit_price=round(price * 2, 2))  # marketable: fills at once
    assert order.status == "filled"
    engine.advance(session_open(day) + timedelta(minutes=first, seconds=10))
    assert engine.halt is not None


def test_changing_a_resting_order_is_allowed_in_a_halt() -> None:
    engine, day, sk = find_halt()
    first, _, _ = sk.halts[0]
    engine.advance(session_open(day) + timedelta(minutes=first - 5))
    broker = Broker(engine, Store(":memory:"))
    price = engine.quotes["SPY"].last
    order = broker.place("SPY", "buy", 1, "limit", limit_price=round(price * 0.5, 2))
    engine.advance(session_open(day) + timedelta(minutes=first, seconds=10))
    assert engine.halt is not None
    new = broker.replace(order.id, limit_price=round(price * 0.4, 2))
    assert new.status == "working"
    with pytest.raises(OrderError, match="halted"):
        broker.place("SPY", "buy", 1, "market")


def test_a_shock_day_is_less_extreme_in_a_crisis() -> None:
    assert engine_mod.SHOCK_IN_CRISIS < 1
    engine = MarketEngine(START, seed=3)
    engine._shocks[START.date()] = (0.6, START)
    sk = engine._skeleton(START.date())
    assert sk.scale == pytest.approx(1.6) and market_day(START) == START.date()
    engine = MarketEngine(START, seed=3)
    day = engine.trigger_shock()  # the trigger's own excess is the smaller share, in its crisis
    low, high = engine_mod.SHOCK_EXCESS_MIN * engine_mod.SHOCK_IN_CRISIS, (engine_mod.SHOCK_EXCESS_MIN + engine_mod.SHOCK_EXCESS_RANGE) * engine_mod.SHOCK_IN_CRISIS
    assert low <= engine._shocks[day][0] <= high


def test_past_halts_are_found_from_the_stored_bars() -> None:
    from paper_trade.market.history import History

    engine, day, sk = find_halt()
    history = History(Store(":memory:").connection, engine)
    # Run the market past the halt day, recording each finished session.
    engine.advance(session_open(day) + timedelta(days=3))
    with history.batch():
        for k in range(0, (engine.time.date() - START.date()).days + 1):
            day_k = START.date() + timedelta(days=k)
            if day_k.weekday() < 5 and day_k < engine.time.date() and day_k in engine._starts:
                history.record(day_k)
    found = history.halts_between(START.date(), engine.time.date())
    assert [h["level"] for h in found] == [level for _, _, level in sk.halts]
    first = sk.halts[0]
    assert found[0]["start"] == int((session_open(day) + timedelta(minutes=first[0])).timestamp())
    assert history.halts_between(day + timedelta(days=1), engine.time.date()) == []  # none after it


def test_the_service_lists_todays_halt_only_once_it_has_begun() -> None:
    from paper_trade.market.clock import SimClock
    from paper_trade.market.service import MarketService

    engine, day, sk = find_halt()
    first = sk.halts[0][0]
    svc = MarketService(SimClock(start=engine.time), engine, Store(":memory:"), history_days=5)
    engine.advance(session_open(day) + timedelta(minutes=first - 3))
    lo, hi = session_open(day) - timedelta(hours=1), session_open(day) + timedelta(hours=7)
    assert svc.halts(lo, hi) == []  # not yet
    engine.advance(session_open(day) + timedelta(minutes=first, seconds=5))
    found = svc.halts(lo, hi)
    assert len(found) >= 1 and found[0]["level"] == 1


def test_a_real_day_gets_an_estimated_halt_without_freezing_prices() -> None:
    from datetime import date

    from paper_trade.market.realbars import RealBars
    from paper_trade.pricehistory import Bar

    day0 = date(2020, 3, 11)  # a Wednesday; the next session falls 9%
    names = [s.symbol for s in engine_mod.SYMBOLS]
    bars = {"SPY": [Bar(day0, 100.0, 101.0, 99.0, 100.0, 1_000_000), Bar(date(2020, 3, 12), 95.0, 95.5, 90.0, 91.0, 5_000_000)]}
    engine = MarketEngine(session_open(date(2020, 3, 11)), seed=3, real=RealBars(names, bars), prices=np.array([100.0 if n == "SPY" else s.price for n, s in zip(names, engine_mod.SYMBOLS)]))
    engine.advance(session_open(date(2020, 3, 12)))
    sk = engine._skeleton(date(2020, 3, 12))
    assert not sk.halts and sk.halted is not None and not sk.halted.any()  # real prices aren't frozen
    found = engine.halts_of(date(2020, 3, 12))
    assert [(h["level"], h["estimated"]) for h in found] == [(1, True)]
    assert np.exp(sk.low[:, engine.symbol_index("SPY")].min()) == pytest.approx(90.0, rel=1e-6)  # the real low stands


def real_engine(bars_by_day, day):
    from paper_trade.market.realbars import RealBars

    names = [s.symbol for s in engine_mod.SYMBOLS]
    first = bars_by_day[0].day
    engine = MarketEngine(
        session_open(first), seed=3, real=RealBars(names, {"SPY": bars_by_day}),
        prices=np.array([bars_by_day[0].close if n == "SPY" else s.price for n, s in zip(names, engine_mod.SYMBOLS)]),
    )
    engine.advance(session_open(day))
    return engine


def test_estimated_halts_follow_the_2013_rule_and_never_freeze_or_close_real_days() -> None:
    from datetime import date

    from paper_trade.pricehistory import Bar

    def crash(day0: date, day1: date, low: float):
        return [Bar(day0, 100.0, 101.0, 99.0, 100.0, 1_000_000), Bar(day1, 95.0, 95.5, low, low + 1, 5_000_000)]

    # before the rule: nothing is marked
    old = real_engine(crash(date(2008, 10, 8), date(2008, 10, 9), 90.0), date(2008, 10, 9))
    assert old.halts_of(date(2008, 10, 9)) == []
    # a 21% fall on a real day: marked, but as a pause, not as the market closing; prices stay real
    deep = real_engine(crash(date(2020, 3, 11), date(2020, 3, 12), 78.0), date(2020, 3, 12))
    levels = [(h["level"], h["estimated"], h["resume"] is not None) for h in deep.halts_of(date(2020, 3, 12))]
    assert levels[-1] == (3, True, True) and all(est for _, est, _ in levels)
    sk = deep._skeleton(date(2020, 3, 12))
    assert not sk.halted.any() and not sk.halts  # no freezing
    assert deep.halt is None and not deep.halted_at(date(2020, 3, 12), 60 * 200)  # trading and orders go on
