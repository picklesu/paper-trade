import dataclasses
import math
from datetime import UTC, date, datetime

import numpy as np
import pytest

from paper_trade.market import options as o
from paper_trade.market.calendar import MARKET_TZ, session_close
from paper_trade.market.engine import MarketEngine
from paper_trade.market.symbols import SYMBOLS

SPEC = {s.symbol: s for s in SYMBOLS}


def et(*args: int) -> datetime:
    return datetime(*args, tzinfo=MARKET_TZ)


def days(symbol: str, now: datetime) -> list[tuple[str, str]]:
    return [(e.day.isoformat(), e.kind) for e in o.expirations(symbol, now)]


def test_expirations_dailies_weeklies_monthlies_and_leaps() -> None:
    spy = days("SPY", et(2026, 1, 14, 12, 0))
    assert spy[:4] == [("2026-01-14", "daily"), ("2026-01-15", "daily"), ("2026-01-16", "monthly"), ("2026-01-20", "daily")]
    assert ("2026-01-19", "daily") not in spy  # MLK Day
    assert ("2027-01-15", "monthly") in spy  # inside the year of monthlies
    assert spy[-2:] == [("2028-01-21", "leaps"), ("2029-01-19", "leaps")]  # the Januaries beyond
    aapl = days("AAPL", et(2026, 1, 14, 12, 0))
    assert aapl[:3] == [("2026-01-16", "monthly"), ("2026-01-23", "weekly"), ("2026-01-30", "weekly")]
    assert all(kind != "daily" for _, kind in aapl)
    assert ("2026-06-18", "monthly") in aapl  # the third Friday, June 19, is Juneteenth: Thursday instead
    # Good Friday: that week's options expire on Thursday.
    assert ("2026-04-02", "weekly") in days("AAPL", et(2026, 3, 30, 10, 0))


def test_expirations_today_end_at_the_close() -> None:
    assert days("SPY", et(2026, 1, 14, 15, 59))[0] == ("2026-01-14", "daily")
    assert days("SPY", session_close(date(2026, 1, 14)))[0] == ("2026-01-15", "daily")


def test_strikes_follow_standard_steps_around_the_price() -> None:
    assert o.strike_step("SPY", 580, 3) == 1 and o.strike_step("AAPL", 230, 10) == 2.5
    assert o.strike_step("XLF", 48, 10) == 1 and o.strike_step("AAPL", 230, 200) == 5
    ks = o.strikes("AAPL", 230.0, 0.25, 30 / 365, 30)
    assert np.allclose(np.diff(ks), 2.5) and ks[0] < 230 < ks[-1] and ks[0] % 2.5 == 0
    far = o.strikes("AAPL", 230.0, 0.3, 2.0, 730)
    assert far[0] >= 230 * math.exp(-o.MAX_STRIKE_RANGE) - 5 and far[-1] <= 230 * math.exp(o.MAX_STRIKE_RANGE) + 5


def test_black_scholes_parity_and_greeks_match_finite_differences() -> None:
    spot, k, t = 100.0, np.array([80.0, 100.0, 125.0]), 0.5
    vol = np.array([0.3, 0.25, 0.22])
    m = o.black_scholes(spot, k, t, vol)
    call, put = m["call"], m["put"]
    assert np.allclose(call["price"] - put["price"], spot - k * math.exp(-o.RATE * t))
    h = 0.01
    up, down = o.black_scholes(spot + h, k, t, vol), o.black_scholes(spot - h, k, t, vol)
    assert np.allclose(call["delta"], (up["call"]["price"] - down["call"]["price"]) / (2 * h), atol=1e-4)
    assert np.allclose(put["gamma"], (up["put"]["delta"] - down["put"]["delta"]) / (2 * h), atol=1e-4)
    vol_up, vol_down = o.black_scholes(spot, k, t, vol + 0.005), o.black_scholes(spot, k, t, vol - 0.005)
    assert np.allclose(call["vega"], vol_up["call"]["price"] - vol_down["call"]["price"], rtol=1e-3)
    sooner, later = o.black_scholes(spot, k, t - 0.5 / 365, vol), o.black_scholes(spot, k, t + 0.5 / 365, vol)
    assert np.allclose(put["theta"], sooner["put"]["price"] - later["put"]["price"], rtol=1e-3)
    higher = o.black_scholes(spot, k, t, vol, rate=o.RATE + 0.01)
    assert np.allclose(call["rho"], higher["call"]["price"] - call["price"], rtol=0.05)


def test_normal_cdf_is_accurate() -> None:
    x = np.linspace(-8, 8, 2001)
    exact = np.array([0.5 * (1 + math.erf(v / math.sqrt(2))) for v in x])
    assert np.abs(o._cdf(x) - exact).max() < 1e-7


def test_smile_skews_to_puts_most_for_index_funds() -> None:
    k = np.array([-0.2, 0.0, 0.2])
    for symbol in ("SPY", "AAPL"):
        w = o.smile(0.01, k, symbol)
        assert w[1] == pytest.approx(0.01) and w[0] > w[1] and w[0] > w[2]
    spy, aapl = o.smile(0.01, k, "SPY"), o.smile(0.01, k, "AAPL")
    assert spy[0] / spy[2] > aapl[0] / aapl[2]


def test_atm_volatility_reverts_to_normal_and_adds_earnings() -> None:
    spec = SPEC["AAPL"]
    hot = [math.sqrt(o.atm_variance(spec, 2.0, t, 0) / t) for t in (0.02, 0.25, 2.0)]
    assert hot[0] > hot[1] > hot[2] > spec.vol  # high now, closer to normal further out
    assert math.sqrt(o.atm_variance(spec, 1.0, 0.5, 0) / 0.5) == pytest.approx(spec.vol)
    with_earnings = o.atm_variance(spec, 1.0, 0.1, 1)
    assert with_earnings == pytest.approx(spec.vol**2 * 0.1 + spec.earnings_move**2)


def engine_at(t: datetime) -> MarketEngine:
    e = MarketEngine(datetime(2026, 1, 5, 15, 0, tzinfo=UTC))
    e.advance(t)
    return e


def test_chain_quotes_are_on_ticks_and_bracket_the_model() -> None:
    e = engine_at(et(2026, 1, 14, 12, 0))
    for symbol in ("SPY", "AAPL"):
        exp = o.expirations(symbol, e.time)[3].day
        c = o.chain(e, symbol, exp)
        for side in ("call", "put"):
            bid, ask = np.array(c[side]["bid"]), np.array(c[side]["ask"])
            assert (bid >= 0).all() and (ask > bid).all()
            if symbol != "SPY":  # SPY: pennies throughout
                prices = np.concatenate([bid, ask])
                cents = np.round(prices * 100).astype(int)
                assert (cents[prices > 3.05] % 5 == 0).all()  # nickels above $3
        assert c["call"]["delta"][0] > 0.9 and c["put"]["delta"][-1] < -0.9
        assert c["expected_move"] == pytest.approx(c["spot"] * c["atm_iv"] * math.sqrt(c["years"]), rel=1e-3)


def test_earnings_raise_iv_until_they_pass() -> None:
    # AAPL reports before the Jan 26 session (2026).
    before = engine_at(et(2026, 1, 23, 12, 0))
    feb = date(2026, 2, 20)
    jan = date(2026, 1, 23)
    assert o.chain(before, "AAPL", feb)["earnings"] and not o.chain(before, "AAPL", jan)["earnings"]
    after = engine_at(et(2026, 1, 26, 12, 0))
    iv_before = o.chain(before, "AAPL", feb)["atm_iv"]
    iv_after = o.chain(after, "AAPL", feb)["atm_iv"]
    assert not o.chain(after, "AAPL", feb)["earnings"]
    assert iv_after < iv_before * 0.95  # the crush (allowing for the market's own moves)


def test_chain_refuses_unknown_expirations() -> None:
    e = engine_at(et(2026, 1, 14, 12, 0))
    with pytest.raises(ValueError, match="no 2026-01-17 expiration"):
        o.chain(e, "SPY", date(2026, 1, 17))


def test_activity_is_steady_and_peaks_near_the_money() -> None:
    e = engine_at(et(2026, 1, 14, 12, 0))
    exp = date(2026, 1, 16)
    a, b = o.chain(e, "SPY", exp), o.chain(e, "SPY", exp)
    assert a["call"]["oi"] == b["call"]["oi"]
    oi = np.array(a["call"]["oi"]) + np.array(a["put"]["oi"])
    ks = np.array(a["strikes"])
    near = np.abs(ks - a["spot"]) < 5
    assert oi[near].mean() > 5 * oi[np.abs(ks - a["spot"]) > 40].mean()
    e.advance(et(2026, 1, 14, 15, 0))
    later = o.chain(e, "SPY", exp)
    by_strike = lambda c, f: dict(zip(c["strikes"], c["call"][f]))  # noqa: E731
    both = set(a["strikes"]) & set(later["strikes"])
    assert all(by_strike(later, "oi")[k] == by_strike(a, "oi")[k] for k in both)  # open interest holds all day
    assert sum(by_strike(later, "volume")[k] for k in both) > sum(by_strike(a, "volume")[k] for k in both)


def plain_ratio(inputs, spec, sec):
    """today_ratio without the nudge from the day's moves: the regime's level."""
    sec = np.asarray(sec).astype(int)
    return inputs.ratio[np.where(sec == 0, 0, (sec - 1) // 60 + 1)]


def test_chains_follow_the_market_volatility(monkeypatch) -> None:
    monkeypatch.setattr(o, "today_ratio", plain_ratio)
    monkeypatch.setattr(o, "SELLOFF_SKEW", 0.0)
    e = engine_at(et(2026, 1, 14, 12, 0))
    exp = o.expirations("SPY", e.time)[5].day
    c = o.chain(e, "SPY", exp)
    ratio = float(e.vol_ratio()[e.symbol_index("SPY")])
    t = c["years"]
    theta = o.atm_variance(SPEC["SPY"], ratio, t, 0, o.DEFAULT_PREMIUM)
    q = e.quotes["SPY"]
    # The smile stays anchored at the open: the ATM volatility is read where the price is now.
    expected = math.sqrt(o.smile(theta, np.array([math.log(q.last / q.open)]), "SPY")[0] / t)
    assert c["atm_iv"] == pytest.approx(expected, rel=1e-3)


def test_chain_and_price_agree_at_any_second() -> None:
    """Order matching prices contracts at past seconds; the chain at that moment must agree."""
    e = engine_at(et(2026, 1, 14, 11, 0))
    exp = o.expirations("AAPL", e.time)[3].day
    c = o.chain(e, "AAPL", exp)
    day, sec = o._now(e)
    p = o.price(e, "AAPL", exp, c["strikes"], day, [sec - 1, sec], [100.0, e.quotes["AAPL"].last])
    assert p.call["ask"][1].tolist() == c["call"]["ask"] and p.put["bid"][1].tolist() == c["put"]["bid"]
    k = c["strikes"][len(c["strikes"]) // 2]
    one = o.contract_quote(e, o.Contract("AAPL", exp, "put", k))
    assert one["bid"] == c["put"]["bid"][len(c["strikes"]) // 2]


def test_contract_symbols() -> None:
    c = o.Contract("AAPL", date(2026, 11, 20), "call", 232.5)
    assert c.symbol == "AAPL261120C00232500" and o.Contract.parse(c.symbol) == c
    assert c.label == "AAPL Nov 20 '26 232.5 Call"
    assert o.Contract.parse("AAPL") is None and o.Contract.parse("AAPL261320C00232500") is None
    assert c.intrinsic(240.0) == pytest.approx(7.5) and o.Contract("X", c.expiry, "put", 10).intrinsic(12) == 0


def test_premium_and_calibrated_shape_set_the_prices() -> None:
    e = engine_at(et(2026, 1, 14, 12, 0))
    exp = o.expirations("AAPL", e.time)[6].day
    base = o.chain(e, "AAPL", exp)
    rich = o.chain(e, "AAPL", exp, o.Calibration(premium=((0.01, 1.3), (2.0, 1.3))))
    # (Not exactly in proportion: the smile's curvature eases as the level rises.)
    assert rich["atm_iv"] == pytest.approx(base["atm_iv"] * 1.3 / o.DEFAULT_PREMIUM, rel=3e-3)
    # A real fit with calls dearer than puts (like gold's long end) turns the skew around.
    up = o.chain(e, "AAPL", exp, o.Calibration(as_of=date(2026, 1, 13), rho=((0.1, 0.4),), eta=0.8))
    ks, iv = np.array(up["strikes"]), np.array(up["call"]["iv"])
    lo, hi = np.argmin(abs(ks - up["spot"] * 0.9)), np.argmin(abs(ks - up["spot"] * 1.1))
    assert iv[hi] > iv[lo] and up["calibration"]["as_of"] == "2026-01-13"
    assert np.array(base["call"]["iv"])[lo] > np.array(base["call"]["iv"])[hi]


def test_sticky_strike_and_skew_steepen_when_the_stock_falls(monkeypatch) -> None:
    monkeypatch.setattr(o, "today_ratio", plain_ratio)
    e = engine_at(et(2026, 1, 14, 12, 0))
    q = e.quotes["SPY"]
    exp = o.expirations("SPY", e.time)[5].day
    ratio = float(e.vol_ratio()[e.symbol_index("SPY")])
    years = o.chain(e, "SPY", exp)["years"]
    theta = o.atm_variance(SPEC["SPY"], ratio, years, 0, o.DEFAULT_PREMIUM)
    at_open = math.sqrt(theta / years)
    # The same strikes keep their volatility, so with the price below the open
    # (negative skew) the at-the-money volatility is higher than at the open.
    below = q.last < q.open
    assert (o.chain(e, "SPY", exp)["atm_iv"] > at_open) == below
    # A selloff steepens the skew.
    fell = dataclasses.replace(q, last=q.prev_close * 0.96)
    monkeypatch.setitem(e.quotes, "SPY", fell)
    calm = o.chain(e, "SPY", exp)
    monkeypatch.setattr(o, "SELLOFF_SKEW", 0.0)
    sold = o.chain(e, "SPY", exp)
    ks = np.array(calm["strikes"])
    far = np.argmin(abs(ks - fell.last * 0.9))
    assert calm["put"]["iv"][far] > sold["put"]["iv"][far]


def test_today_ratio_follows_the_move_so_far() -> None:
    def inputs(closes5: list[float]) -> o.OptionInputs:
        return o.OptionInputs(100.0, 100.0, np.ones(391), np.ones(390), np.array(closes5), np.zeros(391), 0, np.zeros(390, dtype=np.uint64), np.full(390, 100.0))

    noon = 150 * 60
    still = inputs([100.0] * 78)
    wild = inputs(list(100 * np.exp(np.cumsum(np.tile([0.01, -0.01], 39)))))
    spec = SPEC["SPY"]
    assert o.today_ratio(still, spec, noon) < 1.0 < o.today_ratio(wild, spec, noon)
    assert o.today_ratio(wild, spec, 60) == 1.0  # too early to say
    # Steady within a 5-minute stretch.
    assert o.today_ratio(wild, spec, noon) == o.today_ratio(wild, spec, noon + 200)


def test_spreads_are_wider_at_the_open_and_sizes_are_shown() -> None:
    e = engine_at(et(2026, 1, 14, 9, 31))
    exp = o.expirations("AAPL", e.time)[3].day
    spec = SPEC["AAPL"]
    day = date(2026, 1, 14)
    inp = e.option_inputs(day, e.symbol_index("AAPL"))
    early = o.spread_factor(inp, spec, day, 60, exp) / inp.spread[1]
    late = o.spread_factor(inp, spec, day, 5400, exp) / inp.spread[90]
    assert early > 1.8 and late == pytest.approx(1, abs=0.01)
    e.advance(et(2026, 1, 14, 11, 0))
    c = o.chain(e, "AAPL", exp)
    sizes = np.array(c["call"]["ask_size"])
    assert (sizes >= 1).all() and c["call"]["bid_size"][0] >= 1
    assert c == o.chain(e, "AAPL", exp)  # steady within a second
    assert c["call"]["ask_size"] != c["put"]["ask_size"]


def test_spreads_widen_before_earnings() -> None:
    # AAPL reports before the Jan 26, 2026 session.
    e = engine_at(et(2026, 1, 22, 12, 0))
    spec, day = SPEC["AAPL"], date(2026, 1, 22)
    inp = e.option_inputs(day, e.symbol_index("AAPL"))
    plain = o.spread_factor(inp, spec, day, 9000, date(2026, 1, 23))  # expires before the report
    assert o.spread_factor(inp, spec, day, 9000, date(2026, 2, 20)) == pytest.approx(plain * o.EARNINGS_SPREAD)


def test_calibrated_volume_follows_the_stock_and_the_real_mix() -> None:
    e = engine_at(et(2026, 1, 14, 12, 0))
    shape = {
        "per_share": 0.05,
        "put_share": 0.25,
        "per_expiry": [0.5, 0.05, 0.02, 0.01, 0.005, 0.001],
        "strikes": {"call": [1.0, 0.8], "put": [-1.0, 0.8]},
    }
    cal = o.Calibration(volume=shape)
    exp = o.expirations("SPY", e.time)[0].day  # today: the first bucket
    c = o.chain(e, "SPY", exp, cal)
    calls, puts = sum(c["call"]["volume"]), sum(c["put"]["volume"])
    expected = 0.05 * 0.5 * e.quotes["SPY"].volume
    assert calls + puts == pytest.approx(expected, rel=0.2)
    assert calls == pytest.approx(3 * puts, rel=0.3)
    ks = np.array(c["strikes"])
    assert ks[np.argmax(c["call"]["volume"])] > c["spot"] > ks[np.argmax(c["put"]["volume"])]
    real_oi = {"per_share": 0.2, "put_share": 0.6, "per_expiry": [0.1] * 6, "strikes": {"call": [1.0, 1.0], "put": [-1.0, 1.0]}}
    with_oi = o.chain(e, "SPY", exp, o.Calibration(volume=shape, open_interest=real_oi))
    total = sum(with_oi["call"]["oi"]) + sum(with_oi["put"]["oi"])
    assert total == pytest.approx(0.2 * 0.1 * SPEC["SPY"].daily_volume, rel=0.1)
    e.advance(et(2026, 1, 14, 14, 0))
    later = o.chain(e, "SPY", exp, cal)
    oi = lambda chain: dict(zip(chain["strikes"], chain["call"]["oi"]))  # noqa: E731
    assert all(oi(later)[k] == oi(c)[k] for k in set(later["strikes"]) & set(c["strikes"]))
    assert sum(later["call"]["volume"]) > calls


def test_a_real_pattern_lists_the_expirations() -> None:
    pattern = o.DEFAULT_PATTERN | {"short_weekdays": [0, 2], "short_days": 12, "quarter_end_months": 6}
    e = engine_at(et(2026, 1, 14, 12, 0))
    days = {x.day: x.kind for x in o.expirations("AAPL", e.time, pattern)}
    assert days[date(2026, 1, 21)] == "daily" and date(2026, 1, 22) not in days
    assert days[date(2026, 3, 31)] == "quarterly"
    c = o.chain(e, "AAPL", date(2026, 3, 31), o.Calibration(pattern=pattern))
    assert c["kind"] == "quarterly"
    with pytest.raises(ValueError):
        o.chain(e, "AAPL", date(2026, 3, 31))  # not listed by default
