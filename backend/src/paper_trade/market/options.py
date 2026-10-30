"""Option chains priced from the simulated market, shaped by real option prices.

Each expiration's implied volatility, in this order:
  - the level is the simulation's own: today's volatility (the engine's
    `vol_ratio` times the symbol's normal vol, nudged by how much the stock has
    moved so far today) drifting back to normal with longer expirations, plus
    the earnings move's variance for expirations that span the simulation's
    earnings dates (gone the day after: the "IV crush");
  - times the volatility risk premium for the tenor (real ATM IV over real
    realized volatility; DEFAULT_PREMIUM without real data), so options cost
    about what they do compared with how much stocks then move;
  - the smile around it: SSVI (Gatheral and Jacquier's surface), its skew and
    curvature fitted to the symbol's real options (`Calibration`), else
    estimates by kind of underlying;
  - within the day, strikes keep their volatility (sticky strike: the smile is
    anchored at the day's open, so a falling stock moves onto the pricier
    puts' side), and the skew steepens in a selloff.
Prices and Greeks are Black-Scholes with a fixed rate and no dividends, for
every strike at once. Bids and asks sit on penny or nickel ticks around the
model price, wider far from the money, for long expirations, when the market is
volatile, just after the open and just before earnings. Sizes are for show;
with real data, volume and open interest follow the symbol's real mix of
expirations, strikes, calls and puts, scaled to the simulated day's trading
(else they're made up).

Expirations follow the symbol's real listing pattern when there's one (e.g.
Monday/Wednesday/Friday weeklies, quarterlies, June and December LEAPS), else:
daily for SPY, QQQ and IWM over the next two weeks; Fridays for six weeks;
monthlies (third Friday) for a year; then the next two Januaries (LEAPS). An
expiration on a holiday moves to the trading day before. Options expire at the
4:00 PM close.
"""

import functools
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import numpy as np

from paper_trade.market.calendar import (
    SESSION_SECONDS,
    add_trading_days,
    is_trading_day,
    market_day,
    nth_weekday,
    session_close,
    session_seconds,
    trading_day_on_or_before,
)
from paper_trade.market.engine import (
    GAP_SHARE,
    TRADING_DAYS_PER_YEAR,
    MarketEngine,
    _intraday_shape,
    _key,
    _unit_hash,
    earnings_days,
)
from paper_trade.market.symbols import SymbolSpec

RATE = 0.04  # the risk-free rate, continuously compounded
YEAR_SECONDS = 365 * 86400
DAILY = frozenset({"SPY", "QQQ", "IWM"})  # daily expirations and penny ticks throughout
# Volatility drifts from today's level back to normal at this rate (per year):
# about a two-month half-life.
VOL_REVERSION = 4.0
# Smile shape (SSVI) without real data: correlation (skew; negative means puts
# are dearer) and curvature, by kind of underlying.
SKEW = {"index": -0.7, "stock": -0.35, "GLD": 0.1, "TLT": -0.15}
CURVATURE = {"index": 1.1, "stock": 0.9}
SMILE_GAMMA = 0.5
INDEX_FUNDS = frozenset({"SPY", "QQQ", "IWM", "XLF", "XLE", "XLV"})
# Implied over realized volatility without real data, and the range real
# estimates are held to (each symbol's is also pulled halfway toward the median
# of all symbols', as one day's figure for one symbol is noisy).
DEFAULT_PREMIUM = 1.12
PREMIUM_RANGE = (1.0, 1.4)
PREMIUM_SHRINK = 0.5
# Tenors (years) at which fitted skew and premium are kept.
TENOR_NODES = (2 / 365, 7 / 365, 14 / 365, 30 / 365, 60 / 365, 120 / 365, 0.5, 1.0, 2.0)
# Days to expiration splitting volume buckets: [0, 1), [1, 8), ... [366, ...).
VOLUME_BUCKETS = (1, 8, 31, 91, 366)
# Within the day: how much the move so far feeds the level (half of the log
# ratio of realized to expected variance by the close), and how much the skew
# steepens per standard deviation the stock is down beyond half of one.
REALIZED_WEIGHT = 0.5
SELLOFF_SKEW = 0.03
OPEN_SPREAD = 1.0  # spreads this much wider at the open, fading over about ten minutes
EARNINGS_SPREAD = 1.25  # and this much wider in the last sessions before earnings
EARNINGS_SPREAD_DAYS = 3
OPEN_INTEREST_DAYS = 6  # open interest is about this many days of volume
STRIKE_SDS = 3.0  # strikes cover this many standard deviations of the move to expiry
MIN_STRIKE_RANGE = 0.12  # and at least this far either side (log-moneyness)
MAX_STRIKE_RANGE = 0.9  # and at most this far (about 0.4 to 2.5 times the price)
MAX_STRIKES = 160
KINDS = ("daily", "weekly", "quarterly", "monthly", "leaps")  # a day listed twice takes the later kind

# The expirations listed without real data (see `expiration_pattern` in calibration.py).
DEFAULT_PATTERN = {
    "short_weekdays": [],
    "short_days": 0,
    "weekly_days": 38,
    "monthly_months": 12,
    "quarterly_months": [],
    "leaps_months": [1],
    "max_months": 36,
    "quarter_end_months": -1,  # -1: none
    "month_end_months": -1,
}
DAILY_PATTERN = DEFAULT_PATTERN | {"short_weekdays": [0, 1, 2, 3, 4], "short_days": 14}


def _interp(nodes: tuple[tuple[float, float], ...], years: float) -> float:
    """Linear in log tenor between nodes, flat beyond them."""
    xs = [math.log(n[0]) for n in nodes]
    return float(np.interp(math.log(max(years, 1e-6)), xs, [n[1] for n in nodes]))


@dataclass(frozen=True)
class Calibration:
    """What real option prices say about one symbol (empty: the defaults)."""

    as_of: date | None = None
    rho: tuple[tuple[float, float], ...] = ()  # (years, SSVI rho)
    eta: float | None = None
    gamma: float = SMILE_GAMMA
    premium: tuple[tuple[float, float], ...] = ()  # (years, implied / realized vol)
    pattern: dict | None = None
    volume: dict | None = None
    open_interest: dict | None = None
    earnings: dict | None = None  # the real next report: day, timing, implied move
    fit_error: float | None = None  # median, in vol points

    @property
    def calibrated(self) -> bool:
        return self.as_of is not None

    def rho_at(self, symbol: str, years: float) -> float:
        return _interp(self.rho, years) if self.rho else SKEW.get(symbol, SKEW[_kind(symbol)])

    def eta_for(self, symbol: str) -> float:
        return self.eta if self.eta is not None else CURVATURE[_kind(symbol)]

    def premium_at(self, years: float) -> float:
        return _interp(self.premium, years) if self.premium else DEFAULT_PREMIUM

    def describe(self) -> dict:
        return {
            "as_of": self.as_of.isoformat() if self.as_of else None,
            "skew": round(self.rho_at("", 30 / 365), 3) if self.rho else None,
            "premium": round(self.premium_at(30 / 365), 3),
            "earnings": self.earnings,
            "fit_error": self.fit_error,
        }


DEFAULT = Calibration()


def calibrations(summaries: dict[str, dict]) -> dict[str, Calibration]:
    """Pricing calibrations from saved summaries (calibration.calibrate), each
    symbol's premium pulled toward the median of all symbols' at each tenor and
    held to PREMIUM_RANGE."""
    by_node: dict[float, list[float]] = {}
    for s in summaries.values():
        for years, v in s.get("premium") or []:
            by_node.setdefault(years, []).append(v)
    median = {years: float(np.median(vs)) for years, vs in by_node.items()}
    out = {}
    for symbol, s in summaries.items():
        shape = s.get("shape") or {}
        premium = tuple(
            (years, round(float(np.clip(PREMIUM_SHRINK * v + (1 - PREMIUM_SHRINK) * median[years], *PREMIUM_RANGE)), 4))
            for years, v in s.get("premium") or []
        )
        out[symbol] = Calibration(
            as_of=date.fromisoformat(s["as_of"]) if shape else None,
            rho=tuple((a, b) for a, b in shape.get("rho") or []),
            eta=shape.get("eta"),
            gamma=shape.get("gamma", SMILE_GAMMA),
            premium=premium,
            pattern=s.get("pattern"),
            volume=s.get("volume"),
            open_interest=s.get("open_interest"),
            earnings=s.get("earnings"),
            fit_error=s.get("fit_error"),
        )
    return out


@dataclass(frozen=True)
class Expiration:
    day: date
    kind: str  # one of KINDS

    def to_dict(self, today: date) -> dict:
        return {"date": self.day.isoformat(), "kind": self.kind, "days": (self.day - today).days}


def _third_friday(year: int, month: int) -> date:
    return nth_weekday(year, month, 4, 3)


def _month(today: date, ahead: int) -> tuple[int, int]:
    return today.year + (today.month - 1 + ahead) // 12, (today.month - 1 + ahead) % 12 + 1


def _month_end(year: int, month: int) -> date:
    return date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1)


def expirations(symbol: str, now: datetime, pattern: dict | None = None) -> list[Expiration]:
    """Expirations still open at market time `now`, soonest first, listed by
    `pattern` (the symbol's real one, else the defaults)."""
    p = pattern or (DAILY_PATTERN if symbol in DAILY else DEFAULT_PATTERN)
    today = market_day(now)
    found: dict[date, str] = {}

    def add(day: date, kind: str) -> None:
        day = trading_day_on_or_before(day)
        if day >= today and (day not in found or KINDS.index(kind) > KINDS.index(found[day])):
            found[day] = kind

    weekdays = set(p["short_weekdays"])
    for n in range(p["short_days"] + 1):
        day = today + timedelta(days=n)
        if day.weekday() in weekdays and is_trading_day(day):
            add(day, "daily")
    friday = today + timedelta(days=(4 - today.weekday()) % 7)
    while (friday - today).days <= p["weekly_days"]:
        add(friday, "weekly")
        friday += timedelta(weeks=1)
    for m in range(p["max_months"] + 1):
        year, month = _month(today, m)
        if m <= p["monthly_months"] or (m <= 12 and month in p["quarterly_months"]):
            add(_third_friday(year, month), "monthly")
        elif m > 12 and month in p["leaps_months"]:
            add(_third_friday(year, month), "leaps")
        if (m <= p["quarter_end_months"] and month % 3 == 0) or m <= p["month_end_months"]:
            add(_month_end(year, month), "quarterly")
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


def atm_variance(spec: SymbolSpec, ratio: float, years: float, earnings: int, premium: float = 1.0) -> float:
    """Total implied variance at the money to expiry: today's volatility drifting
    back to normal, plus each earnings move's variance before expiry, all
    scaled by the volatility risk premium."""
    normal = spec.vol**2
    now = (spec.vol * ratio) ** 2
    k = VOL_REVERSION * years
    average = normal + (now - normal) * (1 - math.exp(-k)) / k if k > 1e-9 else now
    return (average * years + earnings * spec.earnings_move**2) * premium**2


def ssvi_phi_cap(theta, rho):
    """The largest phi free of butterfly arbitrage (Gatheral and Jacquier's
    sufficient conditions, with a little room)."""
    a = 1 + np.abs(rho)
    return 0.98 * np.minimum(4 / (theta * a), np.sqrt(4 / (theta * a)))


def ssvi(theta, k, rho, phi):
    """SSVI total variance at log-moneyness k, given ATM total variance theta."""
    return theta / 2 * (1 + rho * phi * k + np.sqrt((phi * k + rho) ** 2 + 1 - rho**2))


def smile(theta: float, k: np.ndarray, symbol: str, cal: Calibration = DEFAULT, rho: float | None = None, years: float = 0.25) -> np.ndarray:
    """Total variance at log-moneyness k = ln(K/F), given ATM total variance
    theta: the symbol's SSVI surface (`rho` overrides its skew)."""
    if rho is None:
        rho = cal.rho_at(symbol, years)
    gamma = cal.gamma
    phi = cal.eta_for(symbol) / (theta**gamma * (1 + theta) ** (1 - gamma))
    return ssvi(theta, k, rho, min(phi, float(ssvi_phi_cap(theta, rho))))


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
    expirations and by `market_factor` (wide spreads in the stock, the open,
    earnings)."""
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


def spread_factor(engine: MarketEngine, spec: SymbolSpec, expiry: date) -> float:
    """How much wider than usual option spreads are now: the stock's own spread
    factor, wider in the first minutes and in the sessions before earnings."""
    now = engine.time
    factor = engine.spread_factor(now) * (1 + OPEN_SPREAD * math.exp(-session_seconds(now) / 600))
    today = market_day(now)
    soon = earnings_days(spec, today + timedelta(days=1), min(expiry, add_trading_days(today, EARNINGS_SPREAD_DAYS)))
    return factor * (EARNINGS_SPREAD if soon else 1.0)


def quote_sizes(
    engine: MarketEngine, spec: SymbolSpec, expiry: date, kind: str, strikes_: np.ndarray, delta: np.ndarray, years: float, factor: float
) -> tuple[np.ndarray, np.ndarray]:
    """Contracts shown at the bid and ask of `kind` ("call" or "put"): deepest
    near the money, in short expirations and heavily traded symbols, thinner
    when spreads are wide. Steady within a second (hashed from the contract and
    the second)."""
    a = np.abs(delta)
    near = 0.3 + 0.7 * 4 * a * (1 - a)
    mean = 1 + 40 * math.sqrt(spec.daily_volume / 1e7) * near / (1 + years) / factor
    second = np.uint64(int(engine.time.timestamp()))
    out = []
    for side in (0, 1):
        keys = np.uint64(_key(f"size:{spec.symbol}:{expiry.isoformat()}:{kind}:{side}")) + np.round(strikes_ * 100).astype(np.uint64)
        u = _unit_hash(keys * np.uint64(7919) + second)
        lots = 1 + np.floor(np.log(u) / np.log1p(-1 / np.maximum(mean, 1.0001)))
        out.append(np.nan_to_num(lots, nan=1.0, posinf=1.0).astype(int))
    return out[0], out[1]


def _real_share(shape: dict, side: str, z: np.ndarray, bucket: int, wider: float = 1.0) -> np.ndarray:
    """Each strike's share of a real total (volume or open interest per share of
    the stock): this expiration's tenor bucket, the side, and the strikes'
    spread in standard deviations."""
    mean, width = shape["strikes"][side]
    weight = np.exp(-0.5 * ((z - mean) / (width * wider)) ** 2)
    weight /= max(weight.sum(), 1e-12)
    side_share = shape["put_share"] if side == "put" else 1 - shape["put_share"]
    return shape["per_share"] * shape["per_expiry"][bucket] * side_share * weight


def activity(engine: MarketEngine, spec: SymbolSpec, expiry: date, kind: str, strikes_: np.ndarray, cal: Calibration = DEFAULT) -> dict[str, np.ndarray]:
    """Volume and open interest. With real data, the day's volume so far is the
    symbol's real contracts per share times the shares traded so far, and open
    interest its real open contracts per share of an average day's trading,
    each shared out like the real ones (this expiration's tenor, calls or puts,
    strikes in standard deviations); else made up: most near the money and at
    monthly expirations. Open interest is set as of the previous close (steady
    all day); strikes' shares are anchored there too, so volume only grows."""
    today = market_day(engine.time)
    quote = engine.quotes[spec.symbol]
    anchor = quote.prev_close
    days = max((expiry - today).days, 1)
    sd = spec.vol * math.sqrt(days / 365)
    z = np.log(strikes_ / anchor) / max(sd, 0.005)
    bucket = volume_bucket((expiry - today).days)
    progress = min(1.0, max(0.02, session_seconds(engine.time) / SESSION_SECONDS))

    def made_up(u: np.ndarray) -> np.ndarray:  # open interest without real data
        base = spec.daily_volume / 3000 * {"daily": 0.6, "weekly": 0.8, "quarterly": 0.8, "monthly": 1.5, "leaps": 0.5}[kind]
        return np.floor(base * np.exp(-0.5 * (np.log(strikes_ / anchor) / max(sd, 0.02) / 1.2) ** 2) * (0.3 + 1.4 * u))

    out = {}
    for side in ("call", "put"):
        keys = np.uint64(_key(f"option:{spec.symbol}:{expiry.isoformat()}:{side}")) + np.round(strikes_ * 100).astype(np.uint64)
        u = _unit_hash(keys)
        v = _unit_hash(keys * np.uint64(31) + np.uint64(today.toordinal()))
        if (shape := cal.open_interest) and side in shape["strikes"]:
            out[f"{side}_oi"] = np.floor(_real_share(shape, side, z, bucket) * spec.daily_volume * (0.7 + 0.6 * u)).astype(int)
        elif (shape := cal.volume) and side in shape["strikes"]:
            # Open interest of about OPEN_INTEREST_DAYS of volume, spread wider.
            full_day = _real_share(shape, side, z, bucket, 1.5) * spec.daily_volume
            out[f"{side}_oi"] = np.floor(full_day * OPEN_INTEREST_DAYS * (0.3 + 1.4 * u)).astype(int)
        else:
            out[f"{side}_oi"] = made_up(u).astype(int)
        if (shape := cal.volume) and side in shape["strikes"]:
            out[f"{side}_volume"] = np.floor(_real_share(shape, side, z, bucket) * quote.volume * (0.5 + v)).astype(int)
        else:
            daily = 2.5 if kind == "daily" else 1
            out[f"{side}_volume"] = np.floor(made_up(u) * (0.05 + 0.4 * v) * progress * daily).astype(int)
    return out


def volume_bucket(days: int) -> int:
    return int(np.searchsorted(VOLUME_BUCKETS, days, side="right"))


@functools.cache
def _intraday_variance_share() -> np.ndarray:
    """(S,) the share of a session's own variance (not the gap's) done by each second."""
    v = np.concatenate([[0.0], np.cumsum(_intraday_shape() ** 2)])
    return v / v[-1]


def today_ratio(engine: MarketEngine, i: int, spec: SymbolSpec, ratio: float) -> float:
    """`ratio` nudged by how much the stock has moved since the open, as 5-minute
    returns, compared with what that volatility would have moved it: up to
    REALIZED_WEIGHT of the log difference by the close."""
    s = min(int(session_seconds(engine.time)), SESSION_SECONDS - 1)
    share = float(_intraday_variance_share()[s])
    if share < 0.02:
        return ratio
    closes = np.concatenate([[engine.quotes[spec.symbol].open], engine.day_closes(5)[:, i]])
    realized = float(np.sum(np.diff(np.log(closes)) ** 2))
    expected = (spec.vol * ratio) ** 2 / TRADING_DAYS_PER_YEAR * (1 - GAP_SHARE) * share
    log_ratio = math.log(np.clip(realized / expected, 0.25, 4.0)) if realized > 0 else math.log(0.25)
    return ratio * math.exp(REALIZED_WEIGHT * share * log_ratio / 2)


def chain(engine: MarketEngine, symbol: str, expiry: date, cal: Calibration = DEFAULT) -> dict:
    """Every strike's call and put for one expiration, in columns."""
    i = engine.symbol_index(symbol)
    spec = engine.specs[i]
    now = engine.time
    found = {e.day: e.kind for e in expirations(symbol, now, cal.pattern)}
    if expiry not in found:
        raise ValueError(f"{symbol} has no {expiry.isoformat()} expiration")
    quote = engine.quotes[symbol]
    spot = quote.last
    years = max((session_close(expiry) - now).total_seconds(), 1.0) / YEAR_SECONDS
    # Today's earnings move (if any) already happened at the open.
    reports = len(earnings_days(spec, market_day(now) + timedelta(days=1), expiry))
    ratio = today_ratio(engine, i, spec, float(engine.vol_ratio()[i]))
    theta = atm_variance(spec, ratio, years, reports, cal.premium_at(years))
    # The skew steepens when the stock is down more than half a day's standard deviation.
    down = -math.log(spot / quote.prev_close) / (spec.vol * ratio / math.sqrt(TRADING_DAYS_PER_YEAR))
    rho = max(-0.95, cal.rho_at(symbol, years) - SELLOFF_SKEW * max(0.0, down - 0.5))
    # Sticky strike: the smile stays where the day opened.
    growth = math.exp(RATE * years)
    atm_vol = math.sqrt(smile(theta, np.array([math.log(spot / quote.open)]), symbol, cal, rho)[0] / years)
    ks = strikes(symbol, spot, atm_vol, years, (expiry - market_day(now)).days)
    vols = np.sqrt(smile(theta, np.log(ks / (quote.open * growth)), symbol, cal, rho) / years)
    model = black_scholes(spot, ks, years, vols)
    factor = spread_factor(engine, spec, expiry)
    sd = atm_vol * math.sqrt(years)
    act = activity(engine, spec, expiry, found[expiry], ks, cal)
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
        "calibration": cal.describe(),
        "strikes": ks.tolist(),
    }
    for side in ("call", "put"):
        m = model[side]
        bid, ask = quote_prices(symbol, m["price"], m["delta"], years, factor)
        bid_size, ask_size = quote_sizes(engine, spec, expiry, side, ks, m["delta"], years, factor)
        out[side] = {
            "bid": bid.tolist(),
            "ask": ask.tolist(),
            "bid_size": np.where(bid > 0, bid_size, 0).tolist(),
            "ask_size": ask_size.tolist(),
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
