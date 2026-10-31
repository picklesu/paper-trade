"""8e: sessions up to the newest real day replay the real bars."""

import math
from datetime import date, datetime, timedelta

import numpy as np
import pytest

from paper_trade.market import calendar
from paper_trade.market import engine as engine_mod
from paper_trade.market.calendar import MARKET_TZ, market_day, next_trading_day, session_open
from paper_trade.market.engine import MarketEngine
from paper_trade.market.realbars import RealBars, RealBarsGone
from paper_trade.market.symbols import SYMBOLS
from paper_trade.pricehistory import Bar

START = datetime(2006, 1, 4, 9, 30, tzinfo=MARKET_TZ)
NAMES = [s.symbol for s in SYMBOLS]


@pytest.fixture(autouse=True)
def _calendar():
    yield
    calendar.set_overrides()


def real_bars(days: int = 8, first: date = date(2006, 1, 4), dividend_on: dict | None = None, odd: bool = False) -> dict[str, list[Bar]]:
    out = {}
    for s in ("SPY", "AAPL", "QQQ"):
        bars, day, price = [], first, {"SPY": 126.0, "AAPL": 10.0, "QQQ": 40.0}[s]
        for k in range(days):
            o = price * (1 + 0.004 * math.sin(k + 1))
            c = o * (1 + 0.012 * math.cos(2 * k + 1))
            hi, lo = max(o, c) * 1.011, min(o, c) * 0.992
            if odd and k == 2:
                hi, lo = o * 0.99, c * 1.01  # a high below the open: sanitized
            div = (dividend_on or {}).get((s, day), 0.0)
            bars.append(Bar(day, o, hi, lo, c, 1_234_567.0 + 1000 * k, div))
            price = c
            day = next_trading_day(day)
        out[s] = bars
    return out


def engine_with(bars: dict[str, list[Bar]], seed: int = 42, ready: bool = True) -> MarketEngine:
    engine = MarketEngine(START, seed=seed, real=RealBars(NAMES, bars))
    if ready:  # generate the sessions' starting states
        engine.advance(session_open(next_trading_day(bars["SPY"][-1].day)))
    return engine


def check_day(engine: MarketEngine, bars: dict[str, list[Bar]], day: date, sanitize: bool = False) -> None:
    sk = engine._skeleton(day)
    for symbol, rows in bars.items():
        i = NAMES.index(symbol)
        b = next(r for r in rows if r.day == day)
        hi, lo = max(b.high, b.open, b.close), min(b.low, b.open, b.close)
        assert math.exp(sk.open[0, i]) == pytest.approx(b.open, rel=1e-9)
        assert math.exp(sk.close[-1, i]) == pytest.approx(b.close, rel=1e-9)
        assert math.exp(sk.high[:, i].max()) == pytest.approx(hi, rel=1e-9)
        assert math.exp(sk.low[:, i].min()) == pytest.approx(lo, rel=1e-9)
        assert sk.volume[:, i].sum() == round(b.volume)
        # a valid bar in every minute
        assert (sk.high[:, i] >= np.maximum(sk.open[:, i], sk.close[:, i]) - 1e-12).all()
        assert (sk.low[:, i] <= np.minimum(sk.open[:, i], sk.close[:, i]) + 1e-12).all()
        assert sk.high[:, i].max() <= math.log(hi) + 1e-12 and sk.low[:, i].min() >= math.log(lo) - 1e-12


def test_every_replayed_day_matches_the_real_bar_exactly() -> None:
    bars = real_bars()
    engine = engine_with(bars)
    for b in bars["SPY"]:
        check_day(engine, bars, b.day)


def test_a_bar_with_a_high_below_the_open_is_made_valid() -> None:
    bars = real_bars(odd=True)
    engine = engine_with(bars)
    check_day(engine, bars, bars["SPY"][2].day)


def test_days_chain_from_the_real_closes_and_seconds_agree_with_minutes() -> None:
    bars = real_bars()
    engine = engine_with(bars)
    day = bars["SPY"][3].day
    i = NAMES.index("SPY")
    assert engine._starts[day].prev_close[i] == pytest.approx(bars["SPY"][2].close, rel=1e-9)
    sec = engine.minute_prices(day, 100, i)
    sk = engine._skeleton(day)
    assert sec[0] == pytest.approx(math.exp(sk.open[100, i]), rel=1e-9)
    assert sec.max() <= math.exp(sk.high[100, i]) * (1 + 1e-9) and sec.min() >= math.exp(sk.low[100, i]) * (1 - 1e-9)


def test_the_replay_is_deterministic_and_a_new_seed_changes_only_the_path_inside() -> None:
    bars = real_bars()
    day = bars["SPY"][4].day
    a, b, c = engine_with(bars), engine_with(bars), engine_with(bars, seed=7)
    assert np.array_equal(a._skeleton(day).close, b._skeleton(day).close)
    sa, sc = a._skeleton(day), c._skeleton(day)
    assert not np.array_equal(sa.close, sc.close)
    i = NAMES.index("SPY")
    assert sa.close[-1, i] == pytest.approx(sc.close[-1, i], rel=1e-12) and sa.high[:, i].max() == pytest.approx(sc.high[:, i].max(), rel=1e-12)


def test_symbols_without_a_bar_keep_their_generated_path() -> None:
    bars = real_bars()
    plain = MarketEngine(START, seed=42)
    engine = engine_with(bars)
    i = NAMES.index("MSFT")
    assert np.array_equal(plain._skeleton(START.date()).open[:, i], engine._skeleton(START.date()).open[:, i])
    assert not np.array_equal(plain._skeleton(START.date()).open[:, NAMES.index("SPY")], engine._skeleton(START.date()).open[:, NAMES.index("SPY")])


def test_after_the_newest_real_day_the_sim_carries_on_from_the_last_close() -> None:
    bars = real_bars(days=4)
    engine = engine_with(bars, ready=False)
    last = bars["SPY"][-1]
    nxt = next_trading_day(last.day)
    engine.advance(session_open(nxt))
    i = NAMES.index("SPY")
    assert engine._starts[nxt].prev_close[i] == pytest.approx(last.close, rel=1e-9)
    sk = engine._skeleton(nxt)
    assert math.exp(sk.open[0, i]) != pytest.approx(last.close, rel=1e-12)  # generated, not replayed
    assert engine.dividend(nxt, NAMES.index("MSFT")) == plain_dividend(nxt)


def plain_schedule(col: int) -> list:
    return MarketEngine(START).dividend_schedule(col, date(2006, 1, 4), date(2006, 1, 12))


def plain_dividend(day: date) -> float:
    other = MarketEngine(START, seed=42)
    other.advance(session_open(day))
    return other.dividend(day, NAMES.index("MSFT"))


def test_a_saved_market_keeps_the_data_it_began_with() -> None:
    bars = real_bars()
    engine = engine_with(bars)
    state = engine.to_state()
    assert state["real_through"] == bars["SPY"][-1].day.isoformat()
    more = real_bars(days=12)
    again = MarketEngine.from_state(state, real=RealBars(NAMES, more))
    assert again._real_through == bars["SPY"][-1].day  # later bars don't change an existing market
    with pytest.raises(RealBarsGone):
        MarketEngine.from_state(state, real=None)
    plain = MarketEngine(START).to_state()
    assert plain["real_through"] is None and MarketEngine.from_state(plain, real=RealBars(NAMES, more))._real_through is None


def test_backfill_ends_exactly_at_the_real_closes() -> None:
    first = date(2005, 12, 1)
    bars = real_bars(days=30, first=first)
    engine = MarketEngine(session_open(next_trading_day(bars["SPY"][-1].day)), real=RealBars(NAMES, bars), prices=np.array([b[-1].close if (b := bars.get(n)) else s.price for n, s in zip(NAMES, SYMBOLS)]))
    for _ in engine.backfill_iter(25):
        pass
    for symbol in ("SPY", "AAPL", "QQQ"):
        i = NAMES.index(symbol)
        assert engine.backfill_scale[i] == pytest.approx(1.0, abs=1e-9)
    day = bars["SPY"][10].day
    check_day(engine, bars, day)


def test_real_dividends_set_ex_dates_and_amounts() -> None:
    ex = date(2006, 1, 6)
    bars = real_bars(dividend_on={("AAPL", ex): 0.1234})
    engine = engine_with(bars)
    i = NAMES.index("AAPL")
    assert engine.dividend(ex, i) == 0.1234
    assert engine.dividend(date(2006, 1, 5), i) == 0.0
    assert engine.dividend_schedule(i, date(2006, 1, 4), date(2006, 1, 12)) == [(ex, pytest.approx(0.1234 / bars['AAPL'][1].close))]
    msft = NAMES.index("MSFT")  # no real bars: the model's schedule
    assert engine.dividend_schedule(msft, date(2006, 1, 4), date(2006, 1, 12)) == plain_schedule(msft)


def test_closures_found_from_real_data() -> None:
    days = [d for d in (date(2001, 9, 7) + timedelta(days=k) for k in range(20)) if d.weekday() < 5 and d not in (date(2001, 9, 12), date(2001, 9, 13), date(2001, 9, 14))]
    bars = {"SPY": [Bar(d, 100, 101, 99, 100, 10) for d in days]}
    closed, opened = RealBars(["SPY"], bars).closures()
    assert closed == {date(2001, 9, 12), date(2001, 9, 13), date(2001, 9, 14)} and opened == set()
    calendar.set_overrides(closed, opened)
    assert not calendar.is_trading_day(date(2001, 9, 12)) and next_trading_day(date(2001, 9, 11)) == date(2001, 9, 17)
    calendar.set_overrides()
    assert calendar.is_trading_day(date(2001, 9, 12))
    good_friday = date(2007, 4, 6)  # a rule holiday that traded (as in 2012 it didn't, but suppose)
    closed, opened = RealBars(["SPY"], {"SPY": [Bar(good_friday - timedelta(days=1), 1, 1, 1, 1, 1), Bar(good_friday, 1, 1, 1, 1, 1), Bar(good_friday + timedelta(days=3), 1, 1, 1, 1, 1)]}).closures()
    assert opened == {good_friday}


def test_load_market_replays_real_bars_and_keeps_a_markets_own_data() -> None:
    from paper_trade.main import load_market
    from paper_trade.store import Store

    bars = real_bars()
    real = RealBars(NAMES, bars)
    store = Store(":memory:")
    _, fresh = load_market(store, 42, real)
    assert fresh._real_through == bars["SPY"][-1].day
    store.set("market", fresh.to_state())
    _, again = load_market(store, 42, None)  # the bars are gone: continues as a new market without them
    assert again._real is None and again._real_through is None
    store.set("market", {**fresh.to_state(), "model": 5})
    _, older = load_market(store, 42, real)
    assert older._real_through == bars["SPY"][-1].day


def test_updated_bars_reach_new_markets_only() -> None:
    bars = real_bars()
    engine = engine_with(bars)
    through = engine._real_through
    engine.use_real(RealBars(NAMES, real_bars(days=12)))
    assert engine._real_through == through
    engine.reset(START)
    assert engine._real_through == real_bars(days=12)["SPY"][-1].day


def test_a_path_far_from_the_real_range_turns_back_instead_of_going_flat() -> None:
    bars = real_bars()
    engine = engine_with(bars)
    i = NAMES.index("SPY")
    for b in bars["SPY"]:
        sk = engine._skeleton(b.day)
        # no long runs of minutes stuck on the real high or low
        stuck = (np.isclose(sk.close[:, i], math.log(max(b.high, b.open, b.close)), atol=1e-12) | np.isclose(sk.close[:, i], math.log(min(b.low, b.open, b.close)), atol=1e-12)).sum()
        assert stuck < 20


def test_volume_totals_match_even_when_the_generated_total_is_tiny() -> None:
    from paper_trade.market.engine import _pin_volume
    from paper_trade.market.realbars import RealDay

    real = RealDay(np.array([True, True]), *(np.array([1.0, 1.0]),) * 4, np.array([1_000_003.0, 7.0]), np.zeros(2))
    out = _pin_volume(np.zeros((390, 2)), real)
    assert out[:, 0].sum() == 1_000_003 and out[:, 1].sum() == 7 and (out >= 0).all()


def vix_rows(bars, level=lambda k: 20.0):
    return [(b.day, level(k)) for k, b in enumerate(bars["SPY"])]


def engine_vix(bars, vix, seed=42) -> MarketEngine:
    engine = MarketEngine(START, seed=seed, real=RealBars(NAMES, bars, vix))
    engine.advance(session_open(next_trading_day(bars["SPY"][-1].day)))
    return engine


def test_the_variance_follows_the_real_vix_of_the_previous_close() -> None:
    bars = real_bars()
    vix = vix_rows(bars, lambda k: 15.0 + 10 * k)
    engine = engine_vix(bars, vix)
    for k, b in enumerate(bars["SPY"][1:], start=1):
        expected = float(np.clip(((vix[k - 1][1] / engine_mod.VIX_BIAS) ** 2 - engine_mod.VIX_BASE2) / engine_mod.VIX_SLOPE, *engine_mod.VARIANCE_RANGE))
        assert engine._starts[b.day].variance == pytest.approx(expected)
    # the first session's start comes from the VIX before it
    assert engine._starts[START.date()].variance == 1.0  # no real VIX before the first bar


def test_the_sim_vix_tracks_the_real_vix_it_is_set_from() -> None:
    bars = real_bars(days=6)
    means = []
    for level in (20.0, 35.0, 60.0):  # (checked on 2007-2012: correlation 0.92, bias +2%; synthetic bars add noise)
        engine = engine_vix(bars, vix_rows(bars, lambda k, v=level: v))
        sim = [engine.vix_bars(b.day)[-1][3] for b in bars["SPY"][1:]]
        means.append(np.mean(sim))
        assert np.mean(sim) == pytest.approx(level, rel=0.25), level
    assert means == sorted(means)


def test_real_days_carry_no_shock_or_sim_crash() -> None:
    bars = real_bars()
    engine = engine_vix(bars, vix_rows(bars))
    for b in bars["SPY"]:
        sk = engine._skeleton(b.day)
        assert sk.shock == 0.0 and not sk.shock_onset
    engine.trigger_shock()  # a user shock still works on the simulated future
    assert engine.shock == 0.0


def test_own_variance_rises_after_a_big_real_move() -> None:
    bars = real_bars()
    calm = engine_vix(bars, vix_rows(bars))
    bars["AAPL"][3] = bars["AAPL"][3]._replace(close=bars["AAPL"][3].open * 1.25, high=bars["AAPL"][3].open * 1.26)
    wild = engine_vix(bars, vix_rows(bars))
    i = NAMES.index("AAPL")
    day = bars["SPY"][4].day
    assert wild._starts[day].own_variance[i] > calm._starts[day].own_variance[i]
    assert wild._starts[day].own_variance[i] == engine_mod.VARIANCE_RANGE[1]


def test_earnings_days_come_from_real_volume_spikes() -> None:
    spec = next(s for s in SYMBOLS if s.symbol == "AAPL")
    nominal = engine_mod.earnings_days(spec, date(2006, 1, 1), date(2006, 3, 31))[0]
    spike = nominal + timedelta(days=6)
    days, d = [], date(2005, 10, 3)
    while d <= date(2006, 3, 31):
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    rows = [Bar(x, 10.0, 10.5, 9.5, 10.0, 1000.0 * (6 if x == spike else 1)) for x in days]
    engine = MarketEngine(START, real=RealBars(NAMES, {"AAPL": rows}))
    i = NAMES.index("AAPL")
    assert spike in engine.earnings_dates(i, date(2006, 1, 1), date(2006, 3, 31)) and nominal not in engine.earnings_dates(i, date(2006, 1, 1), date(2006, 3, 31))
    quiet = [Bar(x, 10.0, 10.5, 9.5, 10.0, 1000.0) for x in days]
    engine = MarketEngine(START, real=RealBars(NAMES, {"AAPL": quiet}))
    assert nominal in engine.earnings_dates(i, date(2006, 1, 1), date(2006, 3, 31))  # no spike: the model's date stands
    msft = NAMES.index("MSFT")  # no bars: the model's dates
    assert engine.earnings_dates(msft, date(2006, 1, 1), date(2006, 6, 30)) == engine_mod.earnings_days(SYMBOLS[msft], date(2006, 1, 1), date(2006, 6, 30))
