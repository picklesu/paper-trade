import math
import random
import statistics
import time
from datetime import date, datetime, timedelta

import numpy as np
import pytest

from paper_trade.market import engine as engine_mod
from paper_trade.market.calendar import MARKET_TZ, add_trading_seconds, session_close
from paper_trade.market.engine import MarketEngine, bid_ask
from paper_trade.market.symbols import SymbolSpec


def et(*args: int) -> datetime:
    return datetime(*args, tzinfo=MARKET_TZ)


START = et(2026, 1, 5, 9, 30)  # Monday open


def prices(engine: MarketEngine) -> dict[str, float]:
    return {s: q.last for s, q in engine.quotes.items()}


def at(t: datetime, seed: int = 42) -> MarketEngine:
    engine = MarketEngine(START, seed=seed)
    engine.advance(t)
    return engine


def test_starts_at_spec_prices_before_any_movement() -> None:
    engine = MarketEngine(START)
    for q in engine.quotes.values():
        assert q.prev_close == next(s.price for s in engine.specs if s.symbol == q.symbol)


def test_same_time_gives_same_prices_however_you_step() -> None:
    target = et(2026, 1, 7, 11, 17, 42)
    one_jump = at(target)
    stepped = MarketEngine(START)
    t = START
    while t < target:
        t = min(add_trading_seconds(t, 7_919), target)
        stepped.advance(t)
    assert prices(one_jump) == prices(stepped)
    assert one_jump.regime == stepped.regime


def test_different_seed_gives_different_prices() -> None:
    t = et(2026, 1, 5, 10, 0)
    assert prices(at(t, seed=1)) != prices(at(t, seed=2))


def test_prices_tick_every_second() -> None:
    a = prices(at(et(2026, 1, 5, 10, 0, 0)))
    b = prices(at(et(2026, 1, 5, 10, 0, 1)))
    assert a != b


def test_quotes_stay_consistent_through_a_day() -> None:
    engine = MarketEngine(START)
    t = START
    for _ in range(389):
        t = add_trading_seconds(t, 60)
        engine.advance(t)
        for q in engine.quotes.values():
            assert q.last > 0
            assert q.bid <= q.last <= q.ask
            assert q.low <= q.last <= q.high
            assert q.low <= q.open <= q.high


def test_new_session_rolls_daily_stats_and_gaps() -> None:
    engine = at(et(2026, 1, 9, 15, 59, 59))  # just before Friday's close
    friday = engine.quotes
    engine.advance(et(2026, 1, 12, 9, 30))  # Monday open
    monday = engine.quotes
    for symbol, q in monday.items():
        # prev_close is exactly the last price shown on Friday.
        assert q.prev_close == friday[symbol].last
        assert q.open == q.last == q.high == q.low
        assert q.volume == 0
    # The open gaps away from the close.
    assert any(abs(q.open - q.prev_close) > 0.001 * q.prev_close for q in monday.values())


def test_volume_grows_through_the_day() -> None:
    a = at(et(2026, 1, 5, 10, 0)).quotes["SPY"].volume
    b = at(et(2026, 1, 5, 15, 0)).quotes["SPY"].volume
    assert 0 < a < b


def test_rewind_replay_reproduces_prices() -> None:
    t1, t2 = et(2026, 1, 6, 10, 0), et(2026, 1, 8, 14, 0)
    engine = at(t2)
    at_t2 = prices(engine)
    engine.rewind(t1, new_future=False)
    assert prices(engine) == prices(at(t1))
    engine.advance(t2)
    assert prices(engine) == at_t2


def test_rewind_new_future_keeps_past_and_changes_future() -> None:
    t1, t2 = et(2026, 1, 6, 10, 0), et(2026, 1, 8, 14, 0)
    engine = at(t2)
    at_t2 = prices(engine)
    engine.rewind(t1, new_future=True)
    assert prices(engine) == prices(at(t1))  # the present is unchanged

    # Later the same day and on later days, prices differ.
    engine.advance(et(2026, 1, 6, 10, 5))
    assert prices(engine) != prices(at(et(2026, 1, 6, 10, 5)))
    engine.advance(t2)
    assert prices(engine) != at_t2

    # Before the rewind point, history is untouched.
    engine.rewind(et(2026, 1, 6, 9, 45), new_future=False)
    assert prices(engine) == prices(at(et(2026, 1, 6, 9, 45)))


def test_replay_after_new_future_replays_the_new_timeline() -> None:
    engine = at(et(2026, 1, 8, 14, 0))
    engine.rewind(et(2026, 1, 6, 10, 0), new_future=True)
    engine.advance(et(2026, 1, 7, 12, 0))
    new_timeline = prices(engine)
    engine.rewind(et(2026, 1, 5, 12, 0), new_future=False)
    engine.advance(et(2026, 1, 7, 12, 0))
    assert prices(engine) == new_timeline


def test_rewind_to_origin_with_new_future_keeps_origin_prices() -> None:
    origin = et(2026, 1, 5, 11, 0)
    engine = MarketEngine(origin)
    start_prices = prices(engine)
    engine.advance(et(2026, 1, 5, 12, 0))
    engine.rewind(origin, new_future=True)
    assert prices(engine) == start_prices


def test_rewind_validates_range() -> None:
    engine = at(et(2026, 1, 6, 10, 0))
    with pytest.raises(ValueError):
        engine.rewind(et(2026, 1, 2, 10, 0), new_future=False)  # before origin
    with pytest.raises(ValueError):
        engine.advance(START)  # advance can't go back


def test_reset_starts_a_new_market() -> None:
    engine = at(et(2026, 1, 6, 10, 0))
    engine.reset(et(2025, 6, 2, 9, 30))
    assert engine.origin == et(2025, 6, 2, 9, 30)
    assert engine.time == engine.origin
    for q in engine.quotes.values():
        assert q.prev_close == next(s.price for s in engine.specs if s.symbol == q.symbol)


def test_daily_volatility_matches_spec(monkeypatch) -> None:
    # Hold the regime at normal so only the spec volatility drives returns.
    monkeypatch.setattr(engine_mod, "REGIMES", (("normal", 1.0, 1e12),))
    spec = SymbolSpec("TEST", "Test", 100, vol=0.30, drift=0, market_corr=0.5, daily_volume=1000)
    engine = MarketEngine(START, seed=3, specs=(spec,))
    closes = []
    t = START
    for _ in range(400):
        t = add_trading_seconds(t, 23_400)  # same time next session
        engine.advance(t)
        closes.append(engine.quotes["TEST"].prev_close)
    returns = [math.log(b / a) for a, b in zip(closes, closes[1:])]
    annualized = statistics.stdev(returns) * math.sqrt(252)
    assert abs(annualized - 0.30) < 0.03


def test_fast_forward_a_year_is_quick() -> None:
    engine = MarketEngine(START)
    began = time.perf_counter()
    engine.advance(et(2027, 1, 4, 10, 0))
    assert time.perf_counter() - began < 15
    assert all(q.last > 0 for q in engine.quotes.values())


def test_quote_change_matches_displayed_prices() -> None:
    q = engine_mod.Quote("X", "X", 100.004, 100.0, 100.01, 100.0, 100.0, 100.0, 99.996, 0)
    d = q.to_dict()
    assert d["last"] == d["prev_close"] == 100.0
    assert d["change"] == 0
    assert d["change_pct"] == 0


def test_bid_ask() -> None:
    assert bid_ask(100.0) == (99.99, 100.0)
    assert bid_ask(580.0) == (579.97, 580.03)
    rng = random.Random(0)
    for price in [1.234, 57.5, 999.999] + [rng.uniform(1, 2000) for _ in range(5000)]:
        bid, ask = bid_ask(price)
        assert bid <= price <= ask
        assert ask - bid < 0.25


def test_session_close_is_not_a_market_time() -> None:
    engine = MarketEngine(START)
    engine.advance(session_close(date(2026, 1, 5)))
    assert engine.time == et(2026, 1, 6, 9, 30)


def test_quotes_at_last_second_of_session() -> None:
    engine = at(session_close(date(2026, 1, 5)) - timedelta(milliseconds=1))
    assert all(q.last > 0 for q in engine.quotes.values())


def test_new_future_at_minute_boundary_keeps_regime_and_prices(monkeypatch) -> None:
    # Regimes that switch about every minute, so a replaced draw would show.
    monkeypatch.setattr(engine_mod, "REGIMES", (("calm", 0.6, 0.01), ("normal", 1.0, 0.01), ("volatile", 2.0, 0.01)))
    t = et(2026, 1, 6, 10, 30)  # whole minute, as the date picker sends
    for seed in range(20):
        engine = at(et(2026, 1, 6, 14, 0), seed=seed)
        engine.rewind(t, new_future=True)
        before = at(t, seed=seed)
        assert prices(engine) == prices(before)
        assert engine.regime == before.regime


def test_generated_through_tracks_furthest_session() -> None:
    engine = at(et(2026, 1, 9, 10, 0))
    engine.rewind(et(2026, 1, 6, 10, 0), new_future=False)
    assert engine.generated_through == date(2026, 1, 9)


def test_save_and_restore_is_exact() -> None:
    import json

    engine = at(et(2026, 1, 8, 14, 0))
    engine.rewind(et(2026, 1, 6, 10, 0), new_future=True)
    engine.advance(et(2026, 1, 9, 11, 0))
    restored = MarketEngine.from_state(json.loads(json.dumps(engine.to_state())))
    assert restored.time == engine.time
    assert prices(restored) == prices(engine)
    restored.advance(et(2026, 1, 14, 12, 0))
    engine.advance(et(2026, 1, 14, 12, 0))
    assert prices(restored) == prices(engine)


def test_prices_at_past_time() -> None:
    t = et(2026, 1, 6, 11, 0)
    engine = at(et(2026, 1, 8, 14, 0))
    assert engine.prices_at(t) == prices(at(t))


def test_bid_ask_arrays_match_scalar() -> None:
    from paper_trade.market.engine import bid_ask_arrays

    rng = random.Random(1)
    prices = [1.234, 57.5, 100.0, 580.0, 999.999] + [rng.uniform(1, 2000) for _ in range(20_000)]
    bids, asks = bid_ask_arrays(np.array(prices))
    for p, b, a in zip(prices, bids, asks):
        assert (float(b), float(a)) == bid_ask(p)


def test_starts_version_changes_only_with_new_sessions() -> None:
    engine = at(et(2026, 1, 5, 10, 0))
    v = engine.starts_version
    engine.advance(et(2026, 1, 5, 15, 0))
    assert engine.starts_version == v
    engine.advance(et(2026, 1, 6, 10, 0))
    assert engine.starts_version > v


# ----- history ---------------------------------------------------------------------


def test_backfill_ends_exactly_at_the_starting_prices() -> None:
    engine = MarketEngine(START)
    start_prices = prices(engine)
    days = list(engine.backfill_iter(30))
    assert len(days) == 30 and days[0] == date(2026, 1, 2) and days == sorted(days, reverse=True)
    assert engine.origin == session_open_of(days[-1])
    assert engine.backfilled
    assert prices(engine) == start_prices  # the present is unchanged
    # The last backfilled session closes at the origin's previous close.
    close = engine.day_prices(days[0])[-1]
    for spec, c in zip(engine.specs, close):
        assert c == pytest.approx(spec.price, rel=1e-12)


def test_rewinding_into_history_replays_it() -> None:
    engine = MarketEngine(START)
    list(engine.backfill_iter(30))
    t = et(2025, 12, 10, 11, 0)
    seen = engine.prices_at(t)
    engine.advance(et(2026, 1, 6, 10, 0))
    later = prices(engine)
    engine.rewind(t, new_future=False)
    assert prices(engine) == pytest.approx(seen, rel=1e-12)
    engine.advance(et(2026, 1, 6, 10, 0))
    assert prices(engine) == pytest.approx(later, rel=1e-9)


def test_backfill_survives_save_and_restore() -> None:
    import json

    engine = MarketEngine(START)
    list(engine.backfill_iter(10))
    restored = MarketEngine.from_state(json.loads(json.dumps(engine.to_state())))
    assert restored.origin == engine.origin and restored.backfilled
    day = market_day_of(restored.origin)
    assert np.allclose(restored.day_prices(day), engine.day_prices(day), rtol=1e-12)


def test_minute_bars_match_quotes() -> None:
    engine = at(et(2026, 1, 5, 10, 15, 30))
    bars = engine.minute_bars(date(2026, 1, 5), through=45 * 60 + 30)
    assert bars.shape[0] == 46
    q = engine.quotes
    for i, spec in enumerate(engine.specs):
        assert bars[-1, i, 3] == pytest.approx(q[spec.symbol].last)
        assert bars[:, i, 1].max() == pytest.approx(q[spec.symbol].high)
        assert bars[:, i, 2].min() == pytest.approx(q[spec.symbol].low)
        assert bars[0, i, 0] == pytest.approx(q[spec.symbol].open)
        assert bars[:, i, 4].sum() == pytest.approx(q[spec.symbol].volume, abs=2)


def test_day_complete_hook_fires_once_per_session() -> None:
    engine = MarketEngine(START)
    done: list[date] = []
    engine.on_day_complete = done.append
    engine.advance(et(2026, 1, 8, 10, 0))
    assert done == [date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7)]


def session_open_of(d: date) -> datetime:
    from paper_trade.market.calendar import session_open

    return session_open(d)


def market_day_of(t: datetime) -> date:
    from paper_trade.market.calendar import market_day

    return market_day(t)
