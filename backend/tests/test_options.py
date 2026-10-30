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
    assert spy[-2:] == [("2027-01-15", "leaps"), ("2028-01-21", "leaps")]
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


def test_chains_follow_the_market_volatility() -> None:
    e = engine_at(et(2026, 1, 14, 12, 0))
    exp = o.expirations("SPY", e.time)[5].day
    base = o.chain(e, "SPY", exp)["atm_iv"]
    ratio = float(e.vol_ratio()[e.symbol_index("SPY")])
    t = o.chain(e, "SPY", exp)["years"]
    expected = math.sqrt(o.atm_variance(SPEC["SPY"], ratio, t, 0) / t)
    assert base == pytest.approx(expected, rel=1e-3)
