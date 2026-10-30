"""Option chains priced from the simulated market (there's no real options data).

Each expiration's implied volatility comes from the symbol's own volatility:
  - ATM: today's level (the engine's `vol_ratio` times the symbol's normal vol)
    drifting back to normal with longer expirations, plus the earnings move's
    variance for expirations that span an earnings date (gone the day after:
    the "IV crush");
  - the smile around it: SSVI (Gatheral and Jacquier's surface), skewed toward
    puts, most for index funds.
Prices and Greeks are Black-Scholes with a fixed rate and no dividends, for
every strike at once. Bids and asks sit on penny or nickel ticks around the
model price, wider far from the money, for long expirations and when the
market is volatile. Volume and open interest are made up, for show.

Expirations: daily for SPY, QQQ and IWM over the next two weeks; Fridays for six
weeks; monthlies (third Friday) for a year; then the next two Januaries (LEAPS).
An expiration on a holiday moves to the trading day before. Options expire at
the 4:00 PM close.
"""

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import numpy as np

from paper_trade.market.calendar import (
    SESSION_SECONDS,
    nth_weekday,
    add_trading_days,
    is_trading_day,
    market_day,
    next_trading_day,
    session_close,
    session_seconds,
    trading_day_on_or_before,
)
from paper_trade.market.engine import MarketEngine, _key, _unit_hash, earnings_days
from paper_trade.market.symbols import SymbolSpec

RATE = 0.04  # the risk-free rate, continuously compounded
YEAR_SECONDS = 365 * 86400
DAILY = frozenset({"SPY", "QQQ", "IWM"})  # daily expirations and penny ticks throughout
DAILY_DAYS = 10  # trading days of dailies
WEEKLY_WEEKS = 6
MONTHLY_MONTHS = 12
LEAPS_YEARS = 2
# Volatility drifts from today's level back to normal at this rate (per year):
# about a two-month half-life.
VOL_REVERSION = 4.0
# Smile shape (SSVI): correlation (skew; negative means puts are dearer) and
# curvature, by kind of underlying.
SKEW = {"index": -0.7, "stock": -0.35, "GLD": 0.1, "TLT": -0.15}
CURVATURE = {"index": 1.1, "stock": 0.9}
SMILE_GAMMA = 0.5
INDEX_FUNDS = frozenset({"SPY", "QQQ", "IWM", "XLF", "XLE", "XLV"})
STRIKE_SDS = 3.0  # strikes cover this many standard deviations of the move to expiry
MIN_STRIKE_RANGE = 0.12  # and at least this far either side (log-moneyness)
MAX_STRIKE_RANGE = 0.9  # and at most this far (about 0.4 to 2.5 times the price)
MAX_STRIKES = 160


@dataclass(frozen=True)
class Expiration:
    day: date
    kind: str  # "daily", "weekly", "monthly" or "leaps"

    def to_dict(self, today: date) -> dict:
        return {"date": self.day.isoformat(), "kind": self.kind, "days": (self.day - today).days}


def _third_friday(year: int, month: int) -> date:
    return nth_weekday(year, month, 4, 3)


def expirations(symbol: str, now: datetime) -> list[Expiration]:
    """Expirations still open at market time `now`, soonest first."""
    today = market_day(now)
    found: dict[date, str] = {}

    def add(day: date, kind: str) -> None:
        day = trading_day_on_or_before(day)
        rank = ("daily", "weekly", "monthly", "leaps")
        if day >= today and (day not in found or rank.index(kind) > rank.index(found[day])):
            found[day] = kind

    if symbol in DAILY:
        first = today if is_trading_day(today) else next_trading_day(today)
        for n in range(DAILY_DAYS):
            add(add_trading_days(first, n), "daily")
    friday = today + timedelta(days=(4 - today.weekday()) % 7)
    for w in range(WEEKLY_WEEKS):
        add(friday + timedelta(weeks=w), "weekly")
    last_monthly = today
    for m in range(MONTHLY_MONTHS + 1):
        year, month = today.year + (today.month - 1 + m) // 12, (today.month - 1 + m) % 12 + 1
        last_monthly = _third_friday(year, month)
        add(last_monthly, "monthly")
    # LEAPS: the next Januaries beyond the monthlies.
    year = last_monthly.year + 1
    for _ in range(LEAPS_YEARS):
        add(_third_friday(year, 1), "leaps")
        year += 1
    # Drop any that expired earlier today (the close has passed).
    return [Expiration(d, found[d]) for d in sorted(found) if session_close(d) > now]


def strike_step(symbol: str, price: float, days: int) -> float:
    if symbol in DAILY:
        step = 1.0
    elif price < 25:
        step = 0.5
    elif price < 100:
        step = 1.0
    elif price < 250:
        step = 2.5
    elif price < 1000:
        step = 5.0
    else:
        step = 10.0
    if days > 60:  # longer expirations list fewer strikes
        step = {0.5: 1.0, 1.0: 2.5, 2.5: 5.0, 5.0: 10.0, 10.0: 25.0}[step]
    return step


def strikes(symbol: str, spot: float, vol: float, years: float, days: int) -> np.ndarray:
    step = strike_step(symbol, spot, days)
    half = min(MAX_STRIKE_RANGE, max(MIN_STRIKE_RANGE, STRIKE_SDS * vol * math.sqrt(max(years, 1 / 365))))
    lo, hi = spot * math.exp(-half), spot * math.exp(half)
    while (hi - lo) / step > MAX_STRIKES:
        step *= 2
    first = max(step, math.floor(lo / step) * step)
    return np.round(np.arange(first, hi + step / 2, step), 2)


def _kind(symbol: str) -> str:
    return "index" if symbol in INDEX_FUNDS else "stock"


def atm_variance(spec: SymbolSpec, ratio: float, years: float, earnings: int) -> float:
    """Total implied variance at the money to expiry: today's volatility drifting
    back to normal, plus each earnings move's variance before expiry."""
    normal = spec.vol**2
    now = (spec.vol * ratio) ** 2
    k = VOL_REVERSION * years
    average = normal + (now - normal) * (1 - math.exp(-k)) / k if k > 1e-9 else now
    return average * years + earnings * spec.earnings_move**2


def smile(theta: float, k: np.ndarray, symbol: str) -> np.ndarray:
    """SSVI total variance at log-moneyness k = ln(K/F), given ATM total variance theta."""
    rho = SKEW.get(symbol, SKEW[_kind(symbol)])
    eta = CURVATURE[_kind(symbol)]
    phi = eta / (theta**SMILE_GAMMA * (1 + theta) ** (1 - SMILE_GAMMA))
    return theta / 2 * (1 + rho * phi * k + np.sqrt((phi * k + rho) ** 2 + 1 - rho**2))


def _cdf(x: np.ndarray) -> np.ndarray:
    """The standard normal CDF, vectorized: Zelen and Severo's approximation
    (Abramowitz and Stegun 26.2.17), accurate to 7.5e-8, a hundredth of a cent
    even on a $1,000 stock."""
    t = 1 / (1 + 0.2316419 * np.abs(x))
    poly = t * (0.319381530 + t * (-0.356563782 + t * (1.781477937 + t * (-1.821255978 + t * 1.330274429))))
    upper = _pdf(x) * poly  # P(Z > |x|)
    return np.where(x >= 0, 1 - upper, upper)


def _pdf(x: np.ndarray) -> np.ndarray:
    return np.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def black_scholes(spot: float, k: np.ndarray, years: float, vol: np.ndarray, rate: float = RATE) -> dict[str, dict]:
    """Prices and Greeks for calls and puts at strikes `k`: theta per calendar
    day, vega and rho per 1 point (of volatility, of the rate)."""
    t = max(years, 1e-6)
    sq = vol * math.sqrt(t)
    d1 = (np.log(spot / k) + (rate + vol * vol / 2) * t) / sq
    d2 = d1 - sq
    disc = math.exp(-rate * t)
    nd1, nd2, pd1 = _cdf(d1), _cdf(d2), _pdf(d1)
    gamma = pd1 / (spot * sq)
    vega = spot * pd1 * math.sqrt(t) / 100
    decay = -spot * pd1 * vol / (2 * math.sqrt(t))
    call = {
        "price": spot * nd1 - k * disc * nd2,
        "delta": nd1,
        "theta": (decay - rate * k * disc * nd2) / 365,
        "rho": k * t * disc * nd2 / 100,
    }
    put = {
        "price": k * disc * (1 - nd2) - spot * (1 - nd1),
        "delta": nd1 - 1,
        "theta": (decay + rate * k * disc * (1 - nd2)) / 365,
        "rho": -k * t * disc * (1 - nd2) / 100,
    }
    for side in (call, put):
        side["gamma"], side["vega"] = gamma, vega
    return {"call": call, "put": put}


def quote_prices(symbol: str, mid: np.ndarray, delta: np.ndarray, years: float, market_factor: float) -> tuple[np.ndarray, np.ndarray]:
    """Bids and asks on ticks ($0.01 below $3, $0.05 above, except penny-wide
    index funds) around the model price: wider far from the money, for long
    expirations and when spreads are wide in the stock."""
    penny = symbol in DAILY
    base = 0.01 if penny else 0.02  # of the price, at the money
    far = 1 + 4 * np.clip(0.5 - np.abs(delta), 0, 0.5)  # 1 at the money, 3 far out
    width = mid * base * far * (1 + 0.3 * years) * market_factor
    tick = np.where(penny | (mid < 3), 0.01, 0.05)
    width = np.maximum(width, tick)
    bid = np.floor((mid - width / 2) / tick + 1e-9) * tick
    ask = np.ceil((mid + width / 2) / tick - 1e-9) * tick
    bid = np.maximum(bid, 0)
    ask = np.maximum(ask, bid + tick)
    return np.round(bid, 2), np.round(ask, 2)


def activity(engine: MarketEngine, spec: SymbolSpec, expiry: date, kind: str, strikes_: np.ndarray) -> dict[str, np.ndarray]:
    """Made-up open interest and volume: most near the money and at monthly
    expirations, more for heavily traded symbols. Open interest is set as of the
    previous close (steady all day); volume grows through the day."""
    today = market_day(engine.time)
    base = spec.daily_volume / 3000 * {"daily": 0.6, "weekly": 0.8, "monthly": 1.5, "leaps": 0.5}[kind]
    anchor = engine.quotes[spec.symbol].prev_close
    sd = spec.vol * math.sqrt(max((expiry - today).days, 1) / 365)
    progress = min(1.0, max(0.02, session_seconds(engine.time) / SESSION_SECONDS))
    z = np.log(strikes_ / anchor) / max(sd, 0.02)
    near = np.exp(-0.5 * (z / 1.2) ** 2)
    out = {}
    for side, n in (("call", 0), ("put", 1)):
        keys = np.uint64(_key(f"option:{spec.symbol}:{expiry.isoformat()}:{side}")) + np.round(strikes_ * 100).astype(np.uint64)
        u = _unit_hash(keys)
        day = np.uint64(today.toordinal())
        v = _unit_hash(keys * np.uint64(31) + day)
        oi = np.floor(base * near * (0.3 + 1.4 * u))
        out[f"{side}_oi"] = oi.astype(int)
        out[f"{side}_volume"] = np.floor(oi * (0.05 + 0.4 * v) * progress * (2.5 if kind == "daily" else 1)).astype(int)
    return out


def chain(engine: MarketEngine, symbol: str, expiry: date) -> dict:
    """Every strike's call and put for one expiration, in columns."""
    i = engine.symbol_index(symbol)
    spec = engine.specs[i]
    now = engine.time
    found = {e.day: e.kind for e in expirations(symbol, now)}
    if expiry not in found:
        raise ValueError(f"{symbol} has no {expiry.isoformat()} expiration")
    spot = engine.quotes[symbol].last
    years = max((session_close(expiry) - now).total_seconds(), 1.0) / YEAR_SECONDS
    # Today's earnings move (if any) already happened at the open.
    reports = len(earnings_days(spec, market_day(now) + timedelta(days=1), expiry))
    theta = atm_variance(spec, float(engine.vol_ratio()[i]), years, reports)
    atm_vol = math.sqrt(theta / years)
    ks = strikes(symbol, spot, atm_vol, years, (expiry - market_day(now)).days)
    forward = spot * math.exp(RATE * years)
    vols = np.sqrt(smile(theta, np.log(ks / forward), symbol) / years)
    model = black_scholes(spot, ks, years, vols)
    factor = engine.spread_factor(now)
    sd = atm_vol * math.sqrt(years)
    act = activity(engine, spec, expiry, found[expiry], ks)
    out: dict = {
        "symbol": symbol,
        "expiration": expiry.isoformat(),
        "kind": found[expiry],
        "days": (expiry - market_day(now)).days,
        "years": round(years, 6),
        "spot": round(spot, 2),
        "rate": RATE,
        "atm_iv": round(atm_vol, 4),
        "expected_move": round(spot * sd, 2),
        "earnings": reports > 0,
        "strikes": ks.tolist(),
    }
    for side in ("call", "put"):
        m = model[side]
        bid, ask = quote_prices(symbol, m["price"], m["delta"], years, factor)
        out[side] = {
            "bid": bid.tolist(),
            "ask": ask.tolist(),
            "mark": np.round((bid + ask) / 2, 3).tolist(),
            "iv": np.round(vols, 4).tolist(),
            "delta": np.round(m["delta"], 4).tolist(),
            "gamma": np.round(m["gamma"], 5).tolist(),
            "theta": np.round(m["theta"], 4).tolist(),
            "vega": np.round(m["vega"], 4).tolist(),
            "rho": np.round(m["rho"], 4).tolist(),
            "volume": act[f"{side}_volume"].tolist(),
            "oi": act[f"{side}_oi"].tolist(),
        }
    return out
