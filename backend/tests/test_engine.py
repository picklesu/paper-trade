import math
import random
import statistics
import time
from datetime import date, datetime, timedelta

import numpy as np
import pytest

from paper_trade.market import engine as engine_mod
from paper_trade.market.calendar import (
    MARKET_TZ,
    add_trading_days,
    add_trading_seconds,
    market_day,
    next_trading_day,
    session_close,
    session_seconds,
)
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
    # Hold the regime at normal, with no volatility clustering or jumps, so only the
    # spec volatility drives returns (test_long_run_volatility_matches_spec covers the rest).
    monkeypatch.setattr(engine_mod, "REGIMES", (("normal", 1.0, 1e12, 0.0),))
    for name in ("GARCH_ALPHA", "GARCH_GAMMA", "GARCH_BETA", "OWN_GARCH_ALPHA", "OWN_GARCH_GAMMA", "OWN_GARCH_BETA"):
        monkeypatch.setattr(engine_mod, name, 0.0)
    for name in ("NEWS_PER_YEAR", "CRASHES_PER_YEAR", "SHOCKS_PER_YEAR"):
        monkeypatch.setattr(engine_mod, name, 0.0)
    monkeypatch.setattr(engine_mod, "SHOCK_VARIANCE", 1.0)
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
    monkeypatch.setattr(engine_mod, "REGIMES", (("calm", 0.6, 0.01, 0.0), ("normal", 1.0, 0.01, 0.0), ("volatile", 2.0, 0.01, 0.0)))
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
    prices = [1.234, 57.5, 100.0, 150.0, 580.0, 999.999] + [rng.uniform(1, 2000) for _ in range(20_000)]
    for factor in (1.0, 1.5, 2.37, 5.0):
        bids, asks = bid_ask_arrays(np.array(prices), factor)
        for p, b, a in zip(prices, bids, asks):
            assert (float(b), float(a)) == bid_ask(p, factor)
            assert b <= p <= a


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
    assert len(days) == 30 and days[-1] == date(2026, 1, 2) and days == sorted(days)
    assert engine.origin == session_open_of(days[0])
    assert engine.backfilled
    assert prices(engine) == start_prices  # the present is unchanged
    # The last backfilled session closes at the origin's previous close.
    close = engine.day_prices(days[-1])[-1]
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


def test_interrupted_backfill_changes_nothing() -> None:
    engine = MarketEngine(START)
    before = (engine.origin, dict(engine._starts), engine.starts_version)
    it = engine.backfill_iter(20)
    for _ in range(7):
        next(it)
    it.close()  # e.g. the server stopped mid-build
    assert (engine.origin, dict(engine._starts), engine.starts_version) == before
    assert not engine.backfilled
    assert all(d >= START.date() for d in engine._skeletons)  # no unit-scale sessions left behind


def test_backfill_keeps_todays_session_cached() -> None:
    engine = at(et(2026, 1, 5, 11, 0))
    today = engine._skeleton(date(2026, 1, 5))
    for _ in engine.backfill_iter(60):
        pass
    assert engine._skeletons.get(date(2026, 1, 5)) is today


# ----- model statistics --------------------------------------------------------------


STATS_SYMBOLS = ("SPY", "TLT", "AAPL", "MSFT", "XOM", "CVX")


@pytest.fixture(scope="module")
def ten_years() -> dict:
    """Ten years of a few symbols: daily opens and closes, the mean absolute
    one-minute move over the first 15 minutes and over midday, each day's
    correlation between minute volume and the size of the minute's move, and
    the spread factor at the open and midday."""
    specs = tuple(s for s in engine_mod.SYMBOLS if s.symbol in STATS_SYMBOLS)
    engine = MarketEngine(START, seed=1, specs=specs)
    days, opens, closes, early, midday, volume_corr, spread_open, spread_mid = [], [], [], [], [], [], [], []
    for day in engine.backfill_iter(2520):
        bars = engine.minute_bars(day)
        days.append(day)
        opens.append(bars[0, :, 0])
        closes.append(bars[-1, :, 3])
        r = np.abs(np.diff(np.log(bars[:, :, 0]), axis=0))
        early.append(r[:15].mean(axis=0))
        midday.append(r[150:240].mean(axis=0))
        move = np.abs(np.log(bars[:, :, 3] / bars[:, :, 0]))
        volume_corr.append([np.corrcoef(move[:, i], bars[:, i, 4])[0, 1] for i in range(len(specs))])
        factor = engine._skeletons[day].spread
        spread_open.append(factor[:5].mean())
        spread_mid.append(factor[150:240].mean())
    return {
        "engine": engine,
        "days": days,
        "open": np.array(opens),
        "close": np.array(closes),
        "early": np.array(early),
        "midday": np.array(midday),
        "volume_corr": np.array(volume_corr),
        "spread_open": np.array(spread_open),
        "spread_mid": np.array(spread_mid),
    }


def returns(ten_years: dict, symbol: str) -> np.ndarray:
    return np.diff(np.log(ten_years["close"][:, ten_years["engine"].symbol_index(symbol)]))


def test_long_run_volatility_matches_spec(ten_years) -> None:
    for spec in ten_years["engine"].specs:
        assert returns(ten_years, spec.symbol).std() * math.sqrt(252) == pytest.approx(spec.vol, rel=0.2)


def test_daily_returns_have_fat_tails(ten_years) -> None:
    x = returns(ten_years, "SPY")
    x = x - x.mean()
    assert (x**4).mean() / (x**2).mean() ** 2 > 5  # a normal distribution's is 3


def test_volatility_clusters_and_rises_after_drops(ten_years) -> None:
    r = returns(ten_years, "SPY")
    a = np.abs(r)
    assert np.mean([np.corrcoef(a[:-k], a[k:])[0, 1] for k in range(1, 11)]) > 0.1
    assert np.corrcoef(r[:-1], a[1:])[0, 1] < -0.03  # the leverage effect


def test_single_stocks_cluster_too(ten_years) -> None:
    a = np.abs(returns(ten_years, "AAPL"))
    assert np.mean([np.corrcoef(a[:-k], a[k:])[0, 1] for k in range(1, 11)]) > 0.05


def test_stocks_move_together_more_on_big_days(ten_years) -> None:
    spy, aapl = returns(ten_years, "SPY"), returns(ten_years, "AAPL")
    calm, big = np.abs(spy) <= np.quantile(np.abs(spy), 0.25), np.abs(spy) >= np.quantile(np.abs(spy), 0.75)
    assert np.corrcoef(spy[big], aapl[big])[0, 1] > np.corrcoef(spy[calm], aapl[calm])[0, 1] + 0.2


def test_sectors_and_treasuries(ten_years) -> None:
    def corr(a: str, b: str) -> float:
        return np.corrcoef(returns(ten_years, a), returns(ten_years, b))[0, 1]

    assert corr("XOM", "CVX") > corr("XOM", "AAPL") + 0.15  # oil moves together
    assert corr("AAPL", "MSFT") > corr("AAPL", "XOM") + 0.1
    assert corr("SPY", "TLT") < -0.1  # Treasuries move against stocks


def test_earnings_gaps(ten_years) -> None:
    i = ten_years["engine"].symbol_index("AAPL")
    gaps = np.abs(np.log(ten_years["open"][1:, i] / ten_years["close"][:-1, i]))
    on = np.array([ten_years["engine"]._earnings_on(d)[i] for d in ten_years["days"][1:]])
    assert 38 <= on.sum() <= 41  # four a year
    assert gaps[on].mean() > 3 * gaps[~on].mean()


def test_ex_dividend_gaps(ten_years) -> None:
    engine = ten_years["engine"]
    i = engine.symbol_index("TLT")  # monthly, 0.33% of the close each time
    gaps = np.log(ten_years["open"][1:, i] / ten_years["close"][:-1, i])
    on = np.array([engine._dividend_on(d)[i] for d in ten_years["days"][1:]])
    assert 118 <= on.sum() <= 122
    assert gaps[on].mean() - gaps[~on].mean() == pytest.approx(math.log1p(-0.04 / 12), abs=0.0015)


def test_dividend_schedule_and_amount() -> None:
    engine = MarketEngine(START)
    ko, tsla = engine.symbol_index("KO"), engine.symbol_index("TSLA")
    day = date(2026, 3, 13)
    assert engine._dividend_on(day)[ko] and not engine._dividend_on(date(2026, 3, 12))[ko]
    assert not engine._dividend_on(day)[tsla]
    engine.advance(et(2026, 3, 16, 10, 0))
    amount = engine.dividend(day, ko)
    close = engine.price_at(date(2026, 3, 12), 23399, ko)
    assert amount == pytest.approx(close * 0.029 / 4, abs=0.02)
    assert engine.dividend(day, tsla) == 0.0


def test_volatility_is_highest_after_the_open(ten_years) -> None:
    assert (ten_years["early"].mean(axis=0) > 1.5 * ten_years["midday"].mean(axis=0)).all()


def test_volume_follows_price_moves(ten_years) -> None:
    corr = np.nanmean(ten_years["volume_corr"], axis=0)
    spy = ten_years["engine"].symbol_index("SPY")
    assert (np.delete(corr, spy) > 0.3).all()  # SPY's moves are all market, so it varies less


def test_spreads_widen_at_the_open(ten_years) -> None:
    assert ten_years["spread_mid"].min() >= 1
    # The intraday shape makes opening spreads about 1.5 times as wide; one 10-year sample
    # varies around that (1.40 with this seed, since crises cap both at the maximum), and
    # no widening at all would be 1.0.
    assert ten_years["spread_open"].mean() > 1.3 * ten_years["spread_mid"].mean()


def test_earnings_schedule() -> None:
    engine = MarketEngine(START)
    aapl, nvda = engine.symbol_index("AAPL"), engine.symbol_index("NVDA")
    assert engine._earnings_on(date(2026, 1, 26))[aapl]
    assert not engine._earnings_on(date(2026, 1, 23))[aapl]
    assert engine._earnings_on(date(2026, 4, 27))[aapl]  # the 26th is a Sunday
    assert engine._earnings_on(date(2026, 5, 22))[nvda]  # NVIDIA reports a month later
    assert not engine._earnings_on(date(2026, 4, 22))[nvda]
    assert not engine._earnings_on(date(2026, 1, 26))[engine.symbol_index("SPY")]


def test_continue_older_model_starts_at_the_saved_time_and_closes() -> None:
    old = at(et(2026, 1, 8, 14, 0)).to_state()
    old.pop("model")
    for start in old["starts"].values():
        del start[2:]  # older saves had no variance
    engine = MarketEngine.continue_older(old)
    assert engine.origin == engine.time == et(2026, 1, 8, 14, 0)
    assert not engine.backfilled
    assert engine.quotes["SPY"].prev_close == old["starts"]["2026-01-08"][0][0]


# ----- seconds, symbols and migration ------------------------------------------------


def test_seconds_agree_with_minute_bars() -> None:
    engine = at(et(2026, 1, 6, 10, 0))
    day = date(2026, 1, 6)
    seconds = engine.day_prices(day).reshape(390, 60, -1)
    bars = engine.minute_bars(day)
    assert np.array_equal(seconds[:, 0], bars[..., 0])
    assert np.array_equal(seconds.max(axis=1), bars[..., 1])
    assert np.array_equal(seconds.min(axis=1), bars[..., 2])
    assert np.array_equal(seconds[:, -1], bars[..., 3])


def test_seconds_move_together() -> None:
    engine = at(et(2026, 1, 6, 10, 0))
    x = np.diff(np.log(engine.day_prices(date(2026, 1, 6))), axis=0)
    inside = np.arange(1, x.shape[0] + 1) % 60 != 0  # moves within a minute
    spy, qqq = engine.symbol_index("SPY"), engine.symbol_index("QQQ")
    assert np.corrcoef(x[inside, spy], x[inside, qqq])[0, 1] > 0.5


def test_adding_a_symbol_leaves_the_others_unchanged() -> None:
    few = tuple(s for s in engine_mod.SYMBOLS if s.symbol in ("SPY", "XOM", "AAPL"))
    t = et(2026, 1, 9, 11, 0)
    small = MarketEngine(START, specs=few)
    small.advance(t)
    full = at(t)
    for spec in few:
        assert np.array_equal(
            small.minute_bars(date(2026, 1, 8))[:, small.symbol_index(spec.symbol)],
            full.minute_bars(date(2026, 1, 8))[:, full.symbol_index(spec.symbol)],
        )
        assert small.quotes[spec.symbol] == full.quotes[spec.symbol]  # seconds too


def test_new_future_mid_minute_starts_at_the_next_minute() -> None:
    t = et(2026, 1, 6, 10, 30, 20)
    engine = at(et(2026, 1, 6, 14, 0))
    replay = at(et(2026, 1, 6, 14, 0))
    engine.rewind(t, new_future=True)
    replay.rewind(t, new_future=False)
    for e in (engine, replay):
        e.advance(et(2026, 1, 6, 10, 30, 59))
    assert prices(engine) == prices(replay)  # the rest of that minute replays
    for e in (engine, replay):
        e.advance(et(2026, 1, 6, 10, 32))
    assert prices(engine) != prices(replay)


def test_continue_older_maps_the_old_symbols() -> None:
    import json

    old = json.loads(json.dumps(at(et(2026, 1, 8, 14, 0)).to_state()))
    legacy = [old["symbols"].index(s) for s in engine_mod.LEGACY_SYMBOLS]
    for d, start in old["starts"].items():  # a version 2 save: 10 symbols, no names
        old["starts"][d] = [[start[0][i] * 1.5 for i in legacy], start[1], start[2]]
    del old["symbols"], old["model"]
    engine = MarketEngine.continue_older(old)
    q = engine.quotes
    for i, symbol in enumerate(engine_mod.LEGACY_SYMBOLS):
        assert q[symbol].prev_close == old["starts"]["2026-01-08"][0][i]
    assert q["XOM"].prev_close == next(s.price for s in engine_mod.SYMBOLS if s.symbol == "XOM")


# ----- uncertainty shocks ------------------------------------------------------------


def _daily_moves(engine: MarketEngine, days: int, symbol: str = "SPY") -> np.ndarray:
    closes = [engine.quotes[symbol].prev_close]
    t = engine.time
    for _ in range(days):
        t = add_trading_seconds(t, 23_400)
        engine.advance(t)
        closes.append(engine.quotes[symbol].prev_close)
    return np.diff(np.log(closes))


def test_a_triggered_shock_drops_the_market_at_the_open_then_volatility_fades(monkeypatch) -> None:
    monkeypatch.setattr(engine_mod, "SHOCKS_PER_YEAR", 0.0)  # only the one we trigger
    specs = tuple(s for s in engine_mod.SYMBOLS if s.symbol in ("SPY", "AAPL"))
    engine = MarketEngine(START, seed=5, specs=specs)
    before = _daily_moves(engine, 60)
    day = engine.trigger_shock()
    assert day == next_trading_day(market_day(engine.time))
    after = _daily_moves(engine, 120)
    excess = engine._shocks[day][0]
    assert 0.4 <= excess <= 0.9
    assert engine._skeleton(day).shock_onset and engine._skeleton(day).shock == pytest.approx(excess)
    assert after[0] < -0.01  # the market drops at the onset (about 2.5% per unit of excess)
    assert np.abs(after[1:31]).mean() > 1.3 * np.abs(before).mean()
    assert np.abs(after[-40:]).mean() < np.abs(after[1:31]).mean()  # it fades
    later = engine._skeleton(add_trading_days(day, 60)).shock
    assert later == pytest.approx(excess / 2, rel=0.05)  # half-life of 60 sessions


def test_shocks_are_saved_and_a_rewind_before_the_trigger_undoes_them(monkeypatch) -> None:
    monkeypatch.setattr(engine_mod, "SHOCKS_PER_YEAR", 0.0)
    specs = tuple(s for s in engine_mod.SYMBOLS if s.symbol == "SPY")
    engine = MarketEngine(START, seed=5, specs=specs)
    engine.advance(et(2026, 1, 12, 11, 0))
    engine.advance(et(2026, 1, 13, 11, 0))
    triggered_at = engine.time
    day = engine.trigger_shock()
    engine.advance(et(2026, 1, 20, 11, 0))
    shocked = engine.quotes["SPY"].last
    restored = MarketEngine.from_state(engine.to_state(), specs)
    assert restored._shocks == engine._shocks and restored.quotes["SPY"].last == shocked
    engine.rewind(triggered_at - timedelta(minutes=5), new_future=False)
    assert engine._shocks == {} and day not in engine._starts
    engine.advance(et(2026, 1, 20, 11, 0))
    assert engine.quotes["SPY"].last != shocked  # the replay has no shock in it
    assert engine._skeleton(day).shock == 0.0


# ----- the volatility index ----------------------------------------------------------


def test_the_volatility_index_is_a_sensible_level_and_follows_volatility() -> None:
    engine = MarketEngine(START, seed=7)
    engine.advance(et(2026, 1, 12, 11, 0))
    quote = engine.vix_quote()
    assert 10 < quote["last"] < 40 and quote["bid"] == quote["ask"] == quote["last"]
    assert quote["low"] <= quote["last"] <= quote["high"] and quote["prev_close"] > 0
    day = market_day(engine.time)
    sk = engine._skeleton(day)
    assert len(sk.vix) == 14
    bars = engine.vix_bars(day)
    assert bars.shape == (13, 5) and (bars[:, 1] >= np.maximum(bars[:, 0], bars[:, 3])).all()
    assert bars[0, 0] == sk.vix[0] and bars[-1, 3] == sk.vix[-1]
    live = engine.vix_bars(day, through=int(session_seconds(engine.time)))
    assert live.shape[0] == int(session_seconds(engine.time)) // 1800 + 1


def test_the_index_rises_in_a_shock(monkeypatch) -> None:
    monkeypatch.setattr(engine_mod, "SHOCKS_PER_YEAR", 0.0)
    engine = MarketEngine(START, seed=7)
    engine.advance(et(2026, 1, 12, 15, 0))
    day = engine.trigger_shock()
    calm = engine.vix_quote()["last"]
    engine.advance(session_close(day) - timedelta(minutes=1))
    shocked = engine.vix_quote()["last"]
    assert shocked > 1.4 * calm
    assert shocked == pytest.approx(engine._skeleton(day).vix[-1], rel=0.02)
