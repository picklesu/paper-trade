import math
from datetime import UTC, date, datetime, timedelta

import numpy as np
import pytest

from paper_trade.market import calibration as c
from paper_trade.market import options as o
from paper_trade.market.calendar import MARKET_TZ, session_close

FETCHED = datetime(2026, 10, 30, 12, 0, tzinfo=MARKET_TZ)
TODAY = date(2026, 10, 30)
SPOT = 100.0
DIVIDEND = 0.015  # the forward comes from parity, so a dividend yield must not matter


def occ(root: str, expiry: date, side: str, strike: float) -> str:
    return f"{root}{expiry:%y%m%d}{side}{round(strike * 1000):08d}"


def surface_vol(k: np.ndarray, years: float, atm: float, rho: float, eta: float, gamma: float) -> np.ndarray:
    theta = atm * atm * years
    phi = eta / (theta**gamma * (1 + theta) ** (1 - gamma))
    return np.sqrt(o.ssvi(theta, k, rho, phi) / years)


def chain_snapshots(
    expiries: list[date],
    atm=lambda years: 0.25,
    rho: float = -0.5,
    eta: float = 0.8,
    gamma: float = 0.5,
    root: str = "XYZ",
    volume_day: date | None = None,
) -> dict[str, dict]:
    """Quotes a cent either side of SSVI prices, on strikes every 2.5% of the price."""
    out = {}
    for expiry in expiries:
        years = (session_close(expiry) - FETCHED).total_seconds() / o.YEAR_SECONDS
        forward = SPOT * math.exp((o.RATE - DIVIDEND) * years)
        ks = SPOT * np.exp(np.arange(-30, 31) * 0.025 * max(1, math.sqrt(years / 0.1)))
        ks = np.round(ks, 2)
        vols = surface_vol(np.log(ks / forward), years, atm(years), rho, eta, gamma)
        df = math.exp(-o.RATE * years)
        calls = df * c.black(forward, ks, years, vols, np.ones(len(ks), bool))
        puts = df * c.black(forward, ks, years, vols, np.zeros(len(ks), bool))
        for k, cp, pp in zip(ks, calls, puts):
            for side, price in (("C", cp), ("P", pp)):
                snap = {"latestQuote": {"bp": round(price - 0.01, 4), "ap": round(price + 0.01, 4), "t": "2026-10-30T15:59:00Z"}}
                if volume_day:
                    z = math.log(k / forward) / (0.25 * math.sqrt(years))
                    snap["dailyBar"] = {"t": f"{volume_day.isoformat()}T04:00:00Z", "v": round(1000 * math.exp(-z * z / 2))}
                out[occ(root, expiry, side, float(k))] = snap
    return out


EXPIRIES = [TODAY + timedelta(days=d) for d in (7, 14, 21, 28, 49, 77, 140, 231, 413, 777)]
EXPIRIES = [e - timedelta(days=(e.weekday() - 4) % 7) for e in EXPIRIES]  # Fridays


def test_parse_occ() -> None:
    assert c.parse_occ("AAPL270115P00530000") == ("AAPL", date(2027, 1, 15), "P", 530.0)
    assert c.parse_occ("SPY261030C00757500") == ("SPY", date(2026, 10, 30), "C", 757.5)
    assert c.parse_occ("TSLA1261030C00100000")[0] == "TSLA1"  # adjusted contracts keep their root
    assert c.parse_occ("junk") is None


def test_implied_vols_invert_black() -> None:
    k = np.array([60.0, 90.0, 100.0, 110.0, 160.0])
    vol = np.array([0.5, 0.3, 0.25, 0.22, 0.3])
    call = np.array([False, False, True, True, True])
    price = c.black(101.0, k, 0.4, vol, call)
    assert np.allclose(c.implied_vols(101.0, k, 0.4, price, call), vol, atol=1e-6)
    # Below intrinsic value or above the stock: no volatility.
    assert np.isnan(c.implied_vols(101.0, np.array([90.0]), 0.4, np.array([10.0]), np.array([True]))).all()


def test_calibration_recovers_the_surface_it_was_priced_with() -> None:
    snaps = chain_snapshots(EXPIRIES, rho=-0.55, eta=0.9, gamma=0.45)
    out = c.calibrate("XYZ", snaps, FETCHED)
    assert out["expirations"] == len(EXPIRIES) and out["fitted"] >= len(EXPIRIES) - 1
    assert out["fit_error"] < 0.2  # vol points
    shape = out["shape"]
    assert all(r == pytest.approx(-0.55, abs=0.03) for _, r in shape["rho"])
    assert shape["eta"] == pytest.approx(0.9, rel=0.1) and shape["gamma"] == pytest.approx(0.45, abs=0.06)
    # Put-call parity finds the forward despite the dividend.
    s = c.slices("XYZ", snaps, FETCHED)[-1]
    assert c.implied_forward(s) == pytest.approx(SPOT * math.exp((o.RATE - DIVIDEND) * s.years), rel=1e-3)


def test_stale_wide_and_foreign_quotes_are_left_out() -> None:
    expiry = EXPIRIES[3]
    snaps = chain_snapshots([expiry])
    good = occ("XYZ", expiry, "C", 100.0)
    snaps[good]["latestQuote"] |= {"bp": 1.0, "ap": 9.0}  # far too wide
    stale = occ("XYZ", expiry, "P", 100.0)
    snaps[stale]["latestQuote"]["t"] = "2026-10-27T15:59:00Z"
    snaps["XYZ1" + good[3:]] = snaps[occ("XYZ", expiry, "C", 102.53)]
    (s,) = c.slices("XYZ", snaps, FETCHED)
    i = int(np.flatnonzero(s.strikes == 100.0)[0])
    assert np.isnan(s.call_bid[i]) and np.isnan(s.put_bid[i]) and np.isfinite(s.call_bid[i + 1])
    assert len(s.strikes) == 61  # the adjusted root's contract isn't mixed in


def test_premium_compares_implied_with_realized_volatility() -> None:
    rng = np.random.default_rng(1)
    closes = 100 * np.exp(np.cumsum(rng.normal(0, 0.20 / math.sqrt(252), 300)))
    rv = c.realized_vol(closes, 252)
    out = c.calibrate("XYZ", chain_snapshots(EXPIRIES, atm=lambda years: rv * 1.2), FETCHED, closes=closes)
    long_end = [v for years, v in out["premium"] if years >= 0.9]
    assert long_end and all(v == pytest.approx(1.2, abs=0.03) for v in long_end)
    # Short tenors compare with half a year of history, not one noisy month.
    assert c.realized_window(7 / 365) == c.REALIZED_MIN_DAYS and c.realized_window(2.0) == 252


def test_realized_vol_leaves_out_earnings_gaps() -> None:
    closes = 100 * np.exp(np.cumsum(np.r_[0, np.full(130, 0.01) * np.tile([1, -1], 65)]))
    closes[100:] *= 1.2  # a 20% gap
    assert c.realized_vol(closes, 126) > 0.25
    assert c.realized_vol(closes, 126, drop=1) == pytest.approx(0.01 * math.sqrt(252), rel=0.01)


def test_earnings_jump_gives_the_implied_move_and_is_left_out_of_the_shape() -> None:
    report = c.Earnings(date(2026, 11, 9), "post-market")  # trades on the 10th
    move = 0.06

    def atm(years: float) -> float:
        expiry_days = years * 365
        spans = FETCHED + timedelta(days=expiry_days) > datetime(2026, 11, 10, 9, 30, tzinfo=MARKET_TZ)
        return math.sqrt(0.25**2 + (move**2 / years if spans else 0))

    snaps = chain_snapshots(EXPIRIES, atm=atm)
    out = c.calibrate("XYZ", snaps, FETCHED, earnings=report)
    assert out["earnings"]["move"] == pytest.approx(move, abs=0.004)
    # The expirations just after the report aren't fitted (their smiles carry the jump).
    assert out["fitted"] == len([e for e in EXPIRIES if not report.event_day <= e < report.event_day + timedelta(days=30)])
    assert c.Earnings(date(2026, 11, 9), "").spans(date(2026, 11, 9)) is None  # can't tell
    assert c.Earnings(date(2026, 11, 9), "pre-market").spans(date(2026, 11, 9)) is True


def test_expiration_pattern_reproduces_the_real_listing() -> None:
    # AAPL's listing on 2026-10-30.
    real = [date.fromisoformat(d) for d in (
        "2026-10-30 2026-11-04 2026-11-06 2026-11-09 2026-11-11 2026-11-13 2026-11-20 2026-11-27 2026-12-04 "
        "2026-12-11 2026-12-18 2027-01-15 2027-02-19 2027-03-19 2027-04-16 2027-06-17 2027-09-17 2027-12-17 "
        "2028-01-21 2028-03-17 2028-06-16 2028-12-15 2029-01-19"
    ).split()]
    p = c.expiration_pattern(real, TODAY)
    assert p["short_weekdays"] == [0, 2] and p["monthly_months"] == 6
    assert p["quarterly_months"] == [6, 9] and p["leaps_months"] == [1, 3, 6, 12]
    listed = [e.day for e in o.expirations("AAPL", FETCHED, p)]
    assert set(real) <= set(listed) and len(listed) - len(real) <= 1
    # The same pattern on another date: Monday/Wednesday shorts, holidays respected.
    later = o.expirations("AAPL", datetime(2027, 1, 13, 10, 0, tzinfo=MARKET_TZ), p)
    days = [(e.day.isoformat(), e.kind) for e in later[:5]]
    assert days[:3] == [("2027-01-13", "daily"), ("2027-01-15", "monthly"), ("2027-01-20", "daily")]
    assert ("2027-01-18", "daily") not in days  # Martin Luther King Jr. Day


def test_volume_shape_from_the_last_session_and_today() -> None:
    yesterday = date(2026, 10, 29)
    snaps = chain_snapshots(EXPIRIES, volume_day=yesterday)
    out = c.calibrate("XYZ", snaps, FETCHED, volume_day=yesterday, stock_volume=1e6)
    v = out["volume"]
    total = sum(bar["dailyBar"]["v"] for bar in snaps.values())
    assert v["per_share"] == pytest.approx(total / 1e6, abs=1e-5) and v["put_share"] == pytest.approx(0.5, abs=0.02)
    assert v["day"] == "2026-10-29" and v["per_expiry"][0] == v["per_expiry"][1]  # no same-day options seen
    assert v["strikes"]["call"][0] == pytest.approx(0, abs=0.3)
    # With today's volume, its mix is used and yesterday's total scaled up for same-day options.
    today = chain_snapshots([TODAY], volume_day=TODAY)
    for key, snap in chain_snapshots(EXPIRIES, volume_day=TODAY).items():
        snaps[key]["prevDailyBar"] = snaps[key]["dailyBar"]
        snaps[key]["dailyBar"] = snap["dailyBar"]
    snaps |= today
    out = c.calibrate("XYZ", snaps, FETCHED, volume_day=yesterday, stock_volume=1e6, live_day=TODAY)
    v2 = out["volume"]
    assert v2["day"] == "2026-10-30" and v2["per_expiry"][0] > 0
    assert v2["per_share"] > v["per_share"]


def test_calibrations_pull_premiums_together_and_hold_them_in_range() -> None:
    def summary(premium: float) -> dict:
        return {"as_of": "2026-10-30", "shape": {"rho": [[0.1, -0.3]], "eta": 0.7, "gamma": 0.5}, "premium": [[0.1, premium]]}

    cals = o.calibrations({"A": summary(1.0), "B": summary(1.2), "C": summary(2.5), "D": {"fetched": "x", "shape": None}})
    assert cals["B"].premium_at(0.1) == pytest.approx(1.2)  # the median
    assert cals["A"].premium_at(0.1) == pytest.approx(1.1)
    assert cals["C"].premium_at(0.1) == o.PREMIUM_RANGE[1]
    assert not cals["D"].calibrated and cals["D"].premium_at(0.1) == o.DEFAULT_PREMIUM
    assert cals["A"].rho_at("A", 2.0) == -0.3 and cals["A"].eta_for("A") == 0.7


def test_fetched_after_hours_keeps_the_trading_day() -> None:
    saturday = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
    out = c.calibrate("XYZ", chain_snapshots(EXPIRIES[2:]), saturday)
    assert out["as_of"] == "2026-10-30"


def test_open_interest_shape() -> None:
    snaps = chain_snapshots(EXPIRIES)
    oi = {}
    for key in snaps:
        _, expiry, side, strike = c.parse_occ(key)
        oi[key] = 300 if side == "P" and strike < 100 else 100
    out = c.calibrate("XYZ", snaps, FETCHED, open_interest=oi, average_volume=1e6)
    shape = out["open_interest"]
    assert shape["per_share"] == pytest.approx(sum(oi.values()) / 1e6, abs=1e-5)
    assert shape["put_share"] > 0.6 and shape["strikes"]["put"][0] < 0 < shape["strikes"]["call"][0] + 0.5
    assert c.calibrate("XYZ", snaps, FETCHED)["open_interest"] is None  # none fetched


def test_pattern_after_a_holiday_thursdays_close() -> None:
    # Fetched after the close on the Thursday before Good Friday 2027 (March 26):
    # that week's options have expired; the weeklies after it still count.
    thursday = date(2027, 3, 25)
    fridays = [date(2027, 4, 2) + timedelta(weeks=w) for w in range(5)]  # Apr 2 ... Apr 30
    p = c.expiration_pattern(fridays, thursday)
    assert p["weekly_days"] == (date(2027, 4, 30) - thursday).days
