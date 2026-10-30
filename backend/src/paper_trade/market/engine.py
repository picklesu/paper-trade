"""Synthetic price engine.

Prices exist only during trading sessions (see `calendar`). Each session is
generated in one go at one-minute resolution, its "skeleton": every minute's
open, high, low, close and volume for every symbol, from random draws seeded by
(seed, date). That makes every moment reproducible: the engine can jump to any
time, forward or back, and get the same prices as if it had run there tick by
tick. Charts, stored bars and the history all come from the skeleton.

Seconds are generated only where they're needed: the current minute (quotes)
and minutes where an order could fill. A minute's seconds form a bridge through
its open, its high and low (at random moments), and its close, so they always
agree with the minute's bar: if a bar's high reaches a stop, the stop triggers
in that minute.

A minute's high and low are drawn from the exact distribution of a Brownian
bridge's maximum and minimum given its open and close (the reflection
principle), shifted for sampling once a second (Broadie, Glasserman & Kou), so
no second-by-second path is needed for the bars.

Model, per symbol: a shared market factor (so symbols move together), a sector
factor, and the symbol's own noise, with:
- a market regime (calm / normal / volatile) that switches minute by minute,
  scaling volatility (the market factor's more than the rest, so stocks move
  together more in turbulent markets) and leaning the drift;
- volatility clustering: the market factor's daily variance follows GJR-GARCH
  (a drop raises it more than a rise), and each symbol's own variance follows
  its own GARCH, so a stock stays jumpy for days after an earnings shock;
- fat tails: part of each day's variance arrives as an overnight gap drawn from
  Student's t, plus earnings gaps on a quarterly schedule, occasional news jumps
  per symbol (between two minutes), and rare market-wide selloffs at the open;
- intraday volatility highest just after the open and raised into the close;
- volume that follows price: busier on big moves and after news;
- spreads that widen with volatility (at the open, in volatile markets).
Each symbol's long-run volatility and drift still match its spec: jumps take
their share of the variance from the continuous part.

Random draws come from separate streams for the market, each sector and each
symbol, keyed by name, so adding a symbol leaves the others' prices unchanged,
and one symbol's seconds can be generated alone.

Going back in time either replays the same future or starts a new one. A new
future is a new "epoch": random draws from its start (rounded up to the next
whole minute) come from a new seed, while everything before it is unchanged.

Each market also gets years of history before its start (`backfill_iter`).
"""

import math
import zlib
from collections import OrderedDict
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date, datetime

import numpy as np

from paper_trade.market.calendar import (
    SESSION_MINUTES,
    SESSION_SECONDS,
    add_trading_days,
    is_trading_day,
    market_day,
    next_trading_day,
    session_close,
    session_open,
    session_seconds,
    snap,
)
from paper_trade.market.symbols import SYMBOLS, SymbolSpec

TRADING_DAYS_PER_YEAR = 252

# Share of a day's price variance that arrives as the overnight gap.
GAP_SHARE = 0.2

# (name, volatility multiplier, average market-hours spent in the regime, drift tilt).
# The tilt (annualized, on the market factor) leans calm periods up and volatile
# ones down; it's centered so the long-run drift is unchanged. The multipliers are
# scaled so the long-run volatility still matches each symbol's spec.
REGIMES = (("calm", 0.6, 12.0, 0.3), ("normal", 1.0, 24.0, 0.0), ("volatile", 2.0, 4.0, -2.0))

# The market factor's annualized volatility: sets betas and the size of selloffs.
MARKET_VOL = 0.16

# Fat tails. Gap draws come from Student's t with GAP_DF degrees of freedom, jump
# sizes from one with JUMP_DF (both scaled to unit variance).
GAP_DF = 5
JUMP_DF = 4
# News: about NEWS_PER_YEAR times a year a symbol jumps between two minutes, with
# a standard deviation of NEWS_SIZE days of its volatility.
NEWS_PER_YEAR = 2.0
NEWS_SIZE = 2.5
# Selloffs: about CRASHES_PER_YEAR times a year the market factor gaps down at the
# open by CRASH_MIN plus an exponential amount averaging CRASH_EXTRA (each symbol
# by its beta).
CRASHES_PER_YEAR = 1.0
CRASH_MIN = 0.015
CRASH_EXTRA = 0.015
EARNINGS_PER_YEAR = 4
EARNINGS_VOLUME = 2.5  # volume multiple on a symbol's earnings day

# GJR-GARCH(1,1) on daily variance, as a multiple of normal (averaging 1). GAMMA is
# the extra response to down days. The market factor's, then each symbol's own.
GARCH_ALPHA, GARCH_GAMMA, GARCH_BETA = 0.02, 0.16, 0.88
OWN_GARCH_ALPHA, OWN_GARCH_GAMMA, OWN_GARCH_BETA = 0.05, 0.05, 0.88
# A safety net, rarely reached: a lower cap would clip the big shocks GARCH needs
# to average 1, and bias volatility low.
VARIANCE_RANGE = (0.25, 25.0)

# Intraday volatility: OPEN_BURST and CLOSE_BURST extra at the open and close,
# fading over BURST_DECAY of the session (about 12 minutes).
OPEN_BURST, CLOSE_BURST, BURST_DECAY = 1.5, 0.5, 0.03

VOLUME_NOISE = 0.5  # lognormal sigma of per-minute volume
# Volume follows price: a minute's volume scales with ACTIVITY_BASE plus the rest
# times its squared standardized move (capped at ACTIVITY_CAP), and news brings a
# burst of NEWS_VOLUME_BURST times more, fading over NEWS_VOLUME_DECAY minutes.
ACTIVITY_BASE, ACTIVITY_CAP = 0.7, 9.0
NEWS_VOLUME_BURST, NEWS_VOLUME_DECAY = 4.0, 5.0

# Spreads widen with volatility (relative to a normal midday minute), up to this.
MAX_SPREAD_FACTOR = 5.0

# A continuous path's maximum, sampled once a second, comes out lower by about
# this many one-second standard deviations (Broadie, Glasserman & Kou, 1997).
DISCRETE_EXTREME_SHIFT = 0.5826

HISTORY_DAYS = TRADING_DAYS_PER_YEAR * 5  # history generated before a market's start
SKELETON_CACHE_DAYS = 32
FINE_CACHE_MINUTES = 64

# Saved markets record the model that generated them; prices from another model
# can't be regenerated (see `MarketEngine.continue_older`).
MODEL_VERSION = 3
# Symbol order of saved arrays from before markets saved their symbols.
LEGACY_SYMBOLS = ("SPY", "QQQ", "IWM", "AAPL", "MSFT", "NVDA", "AMZN", "META", "TSLA", "AMD")

_S = SESSION_SECONDS
_M = SESSION_MINUTES
_SKELETON, _FINE, _QUOTE_SIZES = 1, 2, 3  # random stream kinds
VOLUME_PER_QUOTE_LOT = 4_000_000  # a symbol trading this much a day shows about 2 lots a side


@dataclass(frozen=True)
class Quote:
    symbol: str
    name: str
    last: float
    bid: float
    ask: float
    open: float
    high: float
    low: float
    prev_close: float
    volume: int
    bid_size: int = 0
    ask_size: int = 0

    def to_dict(self) -> dict:
        # Change is computed from the rounded prices so the numbers the UI
        # shows always agree with each other.
        last = round(self.last, 2)
        prev_close = round(self.prev_close, 2)
        change = round(last - prev_close, 2)
        return {
            "symbol": self.symbol,
            "name": self.name,
            "last": last,
            "bid": self.bid,
            "ask": self.ask,
            "open": round(self.open, 2),
            "high": round(self.high, 2),
            "low": round(self.low, 2),
            "prev_close": prev_close,
            "change": change,
            "change_pct": round(change / prev_close * 100, 2),
            "volume": self.volume,
            "bid_size": self.bid_size,
            "ask_size": self.ask_size,
        }


def bid_ask(price: float, factor: float = 1.0) -> tuple[float, float]:
    """Penny-wide spread for most prices, about 1bp for expensive ones, times
    `factor` (wider when the market is volatile)."""
    # Whole cents throughout, with the same operations as bid_ask_arrays, so both agree exactly.
    spread = max(1, math.floor(max(0.01, price * 0.0001) * factor * 100 + 0.5))
    bid = math.floor(price * 100 - spread / 2 + 1e-6)  # the 1e-6 absorbs float noise like 57996.99999
    if bid + spread < price * 100:  # e.g. 1.234: 1.22/1.23; shift up so the quote brackets the price
        bid += 1
    return bid / 100, (bid + spread) / 100


def bid_ask_arrays(prices: np.ndarray, factor: float = 1.0) -> tuple[np.ndarray, np.ndarray]:
    """`bid_ask` for an array of prices, with identical results."""
    spread = np.maximum(1, np.floor(np.maximum(0.01, prices * 0.0001) * factor * 100 + 0.5))
    bid = np.floor(prices * 100 - spread / 2 + 1e-6)
    bid = np.where(bid + spread < prices * 100, bid + 1, bid)
    return bid / 100, (bid + spread) / 100


@dataclass
class _DayStart:
    prev_close: np.ndarray  # (N,) previous session's closing prices
    regime: int  # regime at the end of the previous session
    variance: float = 1.0  # the market factor's variance for the session, as a multiple of normal
    own_variance: np.ndarray | None = None  # (N,) each symbol's own variance, likewise (None: all 1)


@dataclass
class _Draws:
    """A session's random draws for the skeleton. Paths are the market factor,
    then each sector, then each symbol's own noise."""

    gap: np.ndarray  # (P,) unit-variance overnight gap shocks
    within: np.ndarray  # (M, P) shocks from each minute's open to its close
    between: np.ndarray  # (M, P) shocks from each minute's close to the next one's open
    u: np.ndarray  # (M, 2) regime switching
    extremes: np.ndarray  # (M, N, 2) uniforms in (0, 1] for each minute's high and low
    volume_noise: np.ndarray  # (M, N)
    news_minute: np.ndarray  # (N,) news arrives after this minute's close, or -1 for none
    news_size: np.ndarray  # (N,) unit-variance news jump sizes
    earnings: np.ndarray  # (N,) unit-variance earnings moves
    crash_u: float  # a selloff happens if this uniform is small enough
    crash_size: float  # unit exponential
    minute_seed: np.ndarray  # (M,) the seed each minute's seconds are drawn from


@dataclass
class _Skeleton:
    """A session at one-minute resolution, in log prices."""

    regimes: np.ndarray  # (M,) regime index for each minute
    open: np.ndarray  # (M, N) log price at each minute's first second
    high: np.ndarray  # (M, N)
    low: np.ndarray  # (M, N)
    close: np.ndarray  # (M, N) log price at each minute's last second
    volume: np.ndarray  # (M, N)
    cum_volume: np.ndarray  # (M+1, N) volume before each minute
    spread: np.ndarray  # (M,) spread factor
    market_load: np.ndarray  # (N,) each symbol's loading on the market factor (log, per unit)
    sector_load: np.ndarray  # (N,)
    own_load: np.ndarray  # (N,)
    minute_seed: np.ndarray  # (M,)
    next_start: _DayStart  # the following session's starting state


class MarketEngine:
    def __init__(
        self,
        start: datetime,
        seed: int = 42,
        specs: tuple[SymbolSpec, ...] = SYMBOLS,
        prices: np.ndarray | None = None,
    ) -> None:
        """A new market at `start`, its previous closes at `prices` (default: the specs')."""
        self.specs = specs
        vol = np.array([s.vol for s in specs])
        self._rho = np.array([s.market_corr for s in specs])
        self._beta = self._rho * vol / MARKET_VOL
        self._daily_volume = np.array([s.daily_volume for s in specs], dtype=float)
        self._earnings_move = np.array([s.earnings_move for s in specs])
        self._news_sd = NEWS_SIZE * vol / math.sqrt(TRADING_DAYS_PER_YEAR)
        self.sectors = sorted({s.sector for s in specs if s.sector})
        self._sector_of = np.array([self.sectors.index(s.sector) if s.sector else 0 for s in specs])
        self._sector_corr = np.array([s.sector_corr if s.sector else 0.0 for s in specs])
        self._keys = [
            _key("market"),
            *(_key(f"sector:{name}") for name in self.sectors),
            *(_key(f"symbol:{s.symbol}") for s in specs),
        ]

        # Jumps take their share of each symbol's variance from the continuous part.
        crash_mean = CRASH_MIN + CRASH_EXTRA
        earnings_var = EARNINGS_PER_YEAR * self._earnings_move**2
        news_var = NEWS_PER_YEAR * self._news_sd**2
        jump_var = earnings_var + news_var + CRASHES_PER_YEAR * (crash_mean**2 + CRASH_EXTRA**2) * self._beta**2
        self._day_sd = np.sqrt(np.maximum(vol**2 - jump_var, 0.5 * vol**2) / TRADING_DAYS_PER_YEAR)
        # Selloffs would pull the drift down; add their average back.
        self._drift = np.array([s.drift for s in specs]) + CRASHES_PER_YEAR * crash_mean * self._beta
        # GARCH constants, so variances average 1. Jumps add to the squared shocks
        # (on average this much a day, in units of a normal day's variance).
        crash_shock = CRASHES_PER_YEAR * (crash_mean**2 + CRASH_EXTRA**2) / MARKET_VOL**2
        self._omega = 1 - GARCH_BETA - GARCH_ALPHA - GARCH_GAMMA / 2 - (GARCH_ALPHA + GARCH_GAMMA) * crash_shock
        own_day_var = (1 - self._rho**2) * self._day_sd**2
        own_jump_shock = np.divide(
            (earnings_var + news_var) / TRADING_DAYS_PER_YEAR,
            own_day_var,
            out=np.zeros_like(own_day_var),
            where=own_day_var > 0,
        )
        mean_response = OWN_GARCH_ALPHA + OWN_GARCH_GAMMA / 2
        self._own_omega = np.maximum(1 - OWN_GARCH_BETA - mean_response * (1 + own_jump_shock), 0.01)

        self._p_switch = [1 - math.exp(-1 / (r[2] * 60)) for r in REGIMES]
        self._normal = next(i for i, r in enumerate(REGIMES) if r[0] == "normal")
        share = _stationary(self._p_switch)  # long-run share of time in each regime
        mults = np.array([r[1] for r in REGIMES])
        self._volume_mults = mults / (share @ mults)
        self._market_mults = mults / math.sqrt(share @ mults**2)
        self._own_mults = np.sqrt(mults) / math.sqrt(share @ mults)
        tilt = np.array([r[3] for r in REGIMES])
        self._tilt = (tilt - share @ tilt) / (TRADING_DAYS_PER_YEAR * (_S - 1))  # per second

        # Per-second standard deviation weights (in units of a normal day's): row 0
        # is the gap, rows 1.. the session, shaped through the day.
        self._gap_weight = math.sqrt(GAP_SHARE)
        self._second_weight = math.sqrt((1 - GAP_SHARE) / (_S - 1)) * _intraday_shape()  # rows 1..S-1
        x = (np.arange(_M) + 0.5) / _M
        volume_shape = 1 + 2 * (2 * x - 1) ** 2
        self._volume_shape = volume_shape / volume_shape.mean()

        self._seed = seed
        self._seeds_used = 0
        # Called with each session as the engine moves past it (its prices are final then).
        self.on_day_complete: Callable[[date], None] | None = None
        # Set by a finished backfill: what its unit-start prices were multiplied by.
        self.backfill_scale: np.ndarray | None = None
        self._reset(start, seed, prices)

    # ----- time travel -------------------------------------------------------

    @property
    def time(self) -> datetime:
        return self._time

    @property
    def generated_through(self) -> date:
        """The latest session whose starting state is known."""
        return max(self._starts)

    def advance(self, t: datetime) -> None:
        for _ in self.advance_iter(t):
            pass

    def advance_iter(self, t: datetime) -> Iterator[date]:
        """Move forward to `t`, yielding after each session is generated."""
        t = snap(t)
        if t < self._time:
            raise ValueError("advance() only moves forward; use rewind()")
        target_day = market_day(t)
        last = self.generated_through
        while last < target_day:
            sk = self._skeleton(last)
            nxt = next_trading_day(last)
            self._starts[nxt] = sk.next_start
            self.starts_version += 1
            if self.on_day_complete:
                self.on_day_complete(last)
            last = nxt
            yield last
        self._time = t

    def rewind(self, t: datetime, new_future: bool) -> None:
        """Go back to `t`. Prices up to `t` are unchanged; after it they either
        replay exactly or, with `new_future`, are generated from a new seed
        (from the next whole minute)."""
        t = snap(t)
        if not self.origin <= t <= self._time:
            raise ValueError("rewind target must be between origin and now")
        if new_future:
            day = market_day(t)
            first = self._epochs[0]
            self._epochs = [first] + [e for e in self._epochs[1:] if e[0] < t]
            self._epochs.append((t, self._new_seed()))
            self._starts = {d: s for d, s in self._starts.items() if d <= day}
            self.starts_version += 1
            self._forget(lambda d: d >= day)
        self._time = t
        self._quotes = None

    def reset(self, t: datetime, prices: np.ndarray | None = None) -> None:
        """Start a brand-new market at `t`, with fresh prices and no history, its
        previous closes at `prices` (default: the specs')."""
        self._reset(t, self._new_seed(), prices)

    def reanchor(self, prices: np.ndarray) -> None:
        """Start a new market at the current time whose prices right now are
        `prices`. Its history is then generated again (`backfilled` is false)."""
        seed = self._new_seed()
        self._reset(self._time, seed, prices)
        # Prices are multiplicative: scale the previous closes by how far today's
        # moves so far took them from the targets.
        now = np.array([q.last for q in self.quotes.values()])
        self._reset(self._time, seed, prices * prices / now)

    # ----- current state -----------------------------------------------------

    @property
    def quotes(self) -> dict[str, Quote]:
        """Every symbol's quote now (cached until the market moves)."""
        if self._quotes is None or self._quotes[0] != self._time:
            self._quotes = (self._time, self._make_quotes())
        return self._quotes[1]

    def _make_quotes(self) -> dict[str, Quote]:
        day = market_day(self._time)
        sk = self._skeleton(day)
        s = min(int(session_seconds(self._time)), _S - 1)
        m, sec = divmod(s, 60)
        seconds = self._fine(day, m)[: sec + 1]
        high, low = seconds.max(axis=0), seconds.min(axis=0)
        if m:
            high = np.maximum(high, np.exp(sk.high[:m].max(axis=0)))
            low = np.minimum(low, np.exp(sk.low[:m].min(axis=0)))
        volume = sk.cum_volume[m] + sk.volume[m] * sec / 60
        open_ = np.exp(sk.open[0])
        prev_close = self._starts[day].prev_close
        factor = float(sk.spread[m])
        bid_size, ask_size = self._quote_sizes(sk, m, sec, factor)
        quotes = {}
        for i, spec in enumerate(self.specs):
            last = float(seconds[-1, i])
            bid, ask = bid_ask(last, factor)
            quotes[spec.symbol] = Quote(
                symbol=spec.symbol,
                name=spec.name,
                last=last,
                bid=bid,
                ask=ask,
                open=float(open_[i]),
                high=float(high[i]),
                low=float(low[i]),
                prev_close=float(prev_close[i]),
                volume=int(volume[i]),
                bid_size=int(bid_size[i]),
                ask_size=int(ask_size[i]),
            )
        return quotes

    def _quote_sizes(self, sk: _Skeleton, m: int, sec: int, factor: float) -> tuple[np.ndarray, np.ndarray]:
        """Shares shown at the bid and ask: round lots, deeper for heavily traded
        symbols and thinner when spreads are wide. For show only (fills ignore
        them). Hashed from the minute's seed, the second and each symbol's key, so
        the same moment always shows the same sizes and adding a symbol leaves
        the others' alone."""
        keys = np.array(self._keys[1 + len(self.sectors) :], dtype=np.uint64)
        base = np.uint64((int(sk.minute_seed[m]) * 1_000_003 + sec * 7919 + _QUOTE_SIZES) % 2**64)
        u = np.stack([_unit_hash(keys * np.uint64(2) + np.uint64(side) + base) for side in (0, 1)])
        mean_lots = 1 + self._daily_volume / VOLUME_PER_QUOTE_LOT / factor
        lots = 1 + np.floor(np.log(u) / np.log1p(-1 / mean_lots))  # geometric with that mean
        lots = np.nan_to_num(lots, nan=1.0, posinf=1.0)  # mean_lots of 1: always one lot
        return 100 * lots[0].astype(int), 100 * lots[1].astype(int)

    def day_closes(self, step: int = 5) -> np.ndarray:
        """(K, N) today's price at the end of each `step`-minute stretch so far,
        the last being the latest price (for sparklines)."""
        day = market_day(self._time)
        s = min(int(session_seconds(self._time)), _S - 1)
        m = s // 60
        closes = np.exp(self._skeleton(day).close[step - 1 : m : step])
        last = np.array([[q.last for q in self.quotes.values()]])
        return np.concatenate([closes, last]) if s else last

    @property
    def regime(self) -> str:
        """The regime that drove the latest price move (the gap's at the open)."""
        day = market_day(self._time)
        s = min(int(session_seconds(self._time)), _S - 1)
        r = self._starts[day].regime if s == 0 else self._skeleton(day).regimes[(s - 1) // 60]
        return REGIMES[r][0]

    def spread_factor(self, t: datetime) -> float:
        """How many times wider than normal spreads are at market time `t`."""
        t = snap(t)
        s = min(int(session_seconds(t)), _S - 1)
        return float(self._skeleton(market_day(t)).spread[s // 60])

    def backfill_iter(self, days: int = HISTORY_DAYS) -> Iterator[date]:
        """Generate `days` sessions of history before the origin, oldest first,
        yielding each one with its bars readable at unit scale (see below).

        The history runs forward, so volatility clustering and regimes carry
        from day to day, starting from prices of 1.0. Prices are multiplicative,
        so once it's done, multiplying every price by `backfill_scale` (the
        origin's previous closes over the history's last closes) gives exactly
        the history a forward replay from the scaled starts produces, ending at
        the origin's prices. Callers keep what they read until then.

        Nothing is committed until the end: the origin and session starts change
        all at once, so an interrupted build leaves the market as it was (and
        saving mid-build stays consistent).
        """
        origin_day = market_day(self.origin)
        day = add_trading_days(origin_day, -days)
        start = _DayStart(np.ones(len(self.specs)), self._normal)
        unit: dict[date, _DayStart] = {}
        try:
            while day < origin_day:
                unit[day] = start
                sk = self._generate(day, start, self._draws(day))
                self._cache(self._skeletons, day, sk, SKELETON_CACHE_DAYS, keep=market_day(self._time))
                start = sk.next_start
                yield day
                day = next_trading_day(day)
        finally:
            # The unit-scale sessions must never be read as prices, finished or not.
            self._forget(lambda d: d < origin_day)
        scale = self._starts[origin_day].prev_close / start.prev_close
        self._starts.update(
            {d: _DayStart(s.prev_close * scale, s.regime, s.variance, s.own_variance) for d, s in unit.items()}
        )
        self.backfill_scale = scale
        if unit:
            self.origin = session_open(min(unit))
            self.starts_version += 1
        self.backfilled = True

    def minute_bars(self, day: date, through: int | None = None) -> np.ndarray:
        """(minutes, N, 5) one-minute [open, high, low, close, volume] bars for a
        session, for the whole day or only through second `through`."""
        sk = self._skeleton(day)
        bars = np.empty((_M, len(self.specs), 5))
        bars[..., 0] = np.exp(sk.open)
        bars[..., 1] = np.exp(sk.high)
        bars[..., 2] = np.exp(sk.low)
        bars[..., 3] = np.exp(sk.close)
        bars[..., 4] = sk.volume
        if through is None:
            return bars
        m, sec = divmod(min(through, _S - 1), 60)
        bars = bars[: m + 1]
        part = self._fine(day, m)[: sec + 1]
        bars[m, :, 1] = part.max(axis=0)
        bars[m, :, 2] = part.min(axis=0)
        bars[m, :, 3] = part[-1]
        bars[m, :, 4] = sk.volume[m] * sec / 60
        return bars

    def minute_range(self, day: date, col: int) -> tuple[np.ndarray, np.ndarray]:
        """(M,) each minute's high and low prices for one symbol."""
        sk = self._skeleton(day)
        return np.exp(sk.high[:, col]), np.exp(sk.low[:, col])

    def first_hit(
        self,
        day: date,
        col: int,
        lo: int,
        hi: int,
        candidates: np.ndarray,
        test: Callable[[np.ndarray, float], np.ndarray],
    ) -> int | None:
        """The first second in [lo, hi] at which `test(prices, spread_factor)`
        holds for one symbol, looking only in minutes flagged in `candidates`
        ((M,) bool, e.g. from `minute_range`: minutes that can't hit are skipped
        without generating their seconds)."""
        if lo > hi:
            return None
        sk = self._skeleton(day)
        first, last = lo // 60, hi // 60
        for m in first + np.flatnonzero(candidates[first : last + 1]):
            a, b = max(lo, 60 * m) - 60 * m, min(hi, 60 * m + 59) - 60 * m
            prices = self._fine(day, int(m), col)[a : b + 1, col]
            hits = np.flatnonzero(test(prices, float(sk.spread[m])))
            if hits.size:
                return int(60 * m + a + hits[0])
        return None

    def price_at(self, day: date, second: int, col: int) -> float:
        """One symbol's price `second` seconds after a session's open."""
        m, sec = divmod(min(second, _S - 1), 60)
        return float(self._fine(day, m, col)[sec, col])

    def day_prices(self, day: date, col: int | None = None) -> np.ndarray:
        """(S, N) every second of a session, row s being s seconds after the open
        (or (S,) for one symbol). Slow (it generates every minute's seconds): for
        tests and checks."""
        if col is None:
            return np.concatenate([self._fine(day, m) for m in range(_M)])
        return np.concatenate([self._fine(day, m, col)[:, col] for m in range(_M)])

    def prices_at(self, t: datetime) -> dict[str, float]:
        """Last prices at a past market time (between origin and now)."""
        t = snap(t)
        m, sec = divmod(min(int(session_seconds(t)), _S - 1), 60)
        row = self._fine(market_day(t), m)[sec]
        return {s.symbol: float(p) for s, p in zip(self.specs, row)}

    def symbol_index(self, symbol: str) -> int:
        return next(i for i, s in enumerate(self.specs) if s.symbol == symbol)

    # ----- saving --------------------------------------------------------------

    def to_state(self) -> dict:
        """Everything needed to rebuild the market exactly; prices regenerate from seeds."""
        return {
            "origin": self.origin.isoformat(),
            "time": self._time.isoformat(),
            "epochs": [[start.isoformat(), seed] for start, seed in self._epochs],
            "starts": {
                d.isoformat(): [
                    s.prev_close.tolist(),
                    s.regime,
                    s.variance,
                    None if s.own_variance is None else s.own_variance.tolist(),
                ]
                for d, s in self._starts.items()
            },
            "seed": self._seed,
            "seeds_used": self._seeds_used,
            "backfilled": self.backfilled,
            "model": MODEL_VERSION,
            "symbols": [s.symbol for s in self.specs],
        }

    @classmethod
    def from_state(cls, state: dict, specs: tuple[SymbolSpec, ...] = SYMBOLS) -> "MarketEngine":
        engine = cls(datetime.fromisoformat(state["origin"]), seed=state["seed"], specs=specs)
        engine._seeds_used = state["seeds_used"]
        engine.backfilled = state.get("backfilled", False)
        engine._time = datetime.fromisoformat(state["time"])
        engine._epochs = [(datetime.fromisoformat(start), seed) for start, seed in state["epochs"]]
        engine._starts = {date.fromisoformat(d): _start_from(s) for d, s in state["starts"].items()}
        return engine

    @classmethod
    def continue_older(cls, state: dict, specs: tuple[SymbolSpec, ...] = SYMBOLS) -> "MarketEngine":
        """A market saved by another price model (or with other symbols) can't be
        regenerated, so start a new one at its time, from that session's previous
        closes (new symbols start at their spec prices). Its history is then
        generated again (`backfilled` is false)."""
        t = datetime.fromisoformat(state["time"])
        saved = state.get("symbols", LEGACY_SYMBOLS)
        prev = dict(zip(saved, state["starts"][market_day(t).isoformat()][0]))
        prices = np.array([prev.get(s.symbol, s.price) for s in specs])
        engine = cls(t, seed=state["seed"], specs=specs, prices=prices)
        engine._seeds_used = state["seeds_used"]
        return engine

    # ----- internals ---------------------------------------------------------

    def _reset(self, start: datetime, seed: int, prices: np.ndarray | None = None) -> None:
        self.origin = snap(start)
        self._time = self.origin
        self._epochs: list[tuple[datetime, int]] = [(self.origin, seed)]
        if prices is None:
            prices = np.array([s.price for s in self.specs])
        self._starts: dict[date, _DayStart] = {market_day(self.origin): _DayStart(prices, self._normal)}
        self._skeletons: OrderedDict[date, _Skeleton] = OrderedDict()
        self._fines: OrderedDict[tuple[date, int], tuple[np.ndarray, np.ndarray]] = OrderedDict()
        self._quotes: tuple[datetime, dict[str, Quote]] | None = None
        self.backfilled = False
        # Bumped whenever session start states change, so savers can skip the large part.
        self.starts_version = getattr(self, "starts_version", 0) + 1

    def _new_seed(self) -> int:
        self._seeds_used += 1
        return (self._seed * 1_000_003 + self._seeds_used) % 2**63

    def _random(self, seed: int, day: date) -> _Draws:
        """A session's skeleton draws from one seed: one stream per path."""
        n, k = len(self.specs), len(self.sectors)
        p = 1 + k + n
        gap = np.empty(p)
        within = np.empty((_M, p))
        between = np.empty((_M, p))
        extremes = np.empty((_M, n, 2))
        volume_noise = np.empty((_M, n))
        news_minute = np.full(n, -1)
        news_size = np.zeros(n)
        earnings = np.zeros(n)
        gap_scale = math.sqrt((GAP_DF - 2) / GAP_DF)
        jump_scale = math.sqrt((JUMP_DF - 2) / JUMP_DF)
        for j, key in enumerate(self._keys):
            rng = np.random.default_rng([seed, day.toordinal(), _SKELETON, key])
            gap[j] = rng.standard_t(GAP_DF) * gap_scale  # fat-tailed overnight gaps
            within[:, j] = rng.standard_normal(_M)
            between[:, j] = rng.standard_normal(_M)
            if j == 0:
                u = rng.random((_M, 2))
                crash_u, crash_size = rng.random(), rng.exponential()
            elif j > k:
                i = j - 1 - k
                extremes[:, i] = 1 - rng.random((_M, 2))
                volume_noise[:, i] = rng.lognormal(0.0, VOLUME_NOISE, _M)
                if rng.random() < NEWS_PER_YEAR / TRADING_DAYS_PER_YEAR:
                    news_minute[i] = rng.integers(0, _M - 1)
                news_size[i] = rng.standard_t(JUMP_DF) * jump_scale
                earnings[i] = rng.standard_t(JUMP_DF) * jump_scale
        return _Draws(
            gap,
            within,
            between,
            u,
            extremes,
            volume_noise,
            news_minute,
            news_size,
            earnings,
            crash_u,
            crash_size,
            np.full(_M, seed, dtype=np.int64),
        )

    def _draws(self, day: date) -> _Draws:
        """The day's draws, honoring any epochs that start during it.

        A new epoch takes over from the first whole minute at or after its start:
        that minute's moves (from its open on) and everything after. The gap's
        draws (earnings, selloffs) come from the epoch in force at the open.
        """
        open_, close = session_open(day), session_close(day)
        before = [e for e in self._epochs if e[0] < open_]
        base = before[-1] if before else self._epochs[0]
        draws = self._random(base[1], day)
        for epoch in self._epochs:
            start, seed = epoch
            if epoch is base or not open_ <= start < close:
                continue
            m = math.ceil(session_seconds(start) / 60)
            if m >= _M:
                continue
            new = self._random(seed, day)
            for name in ("within", "between", "u", "extremes", "volume_noise", "minute_seed"):
                getattr(draws, name)[m:] = getattr(new, name)[m:]
            # News after the minute it arrives in: earlier news stays; later, the new epoch's.
            keep = (draws.news_minute >= 0) & (draws.news_minute < m)
            draws.news_minute = np.where(keep, draws.news_minute, np.where(new.news_minute >= m, new.news_minute, -1))
            draws.news_size = np.where(keep, draws.news_size, new.news_size)
        return draws

    def _skeleton(self, day: date) -> _Skeleton:
        if day in self._skeletons:
            self._skeletons.move_to_end(day)
            return self._skeletons[day]
        sk = self._generate(day, self._starts[day], self._draws(day))
        self._cache(self._skeletons, day, sk, SKELETON_CACHE_DAYS, keep=market_day(self._time))
        return sk

    def _generate(self, day: date, start: _DayStart, draws: _Draws) -> _Skeleton:
        n, k = len(self.specs), len(self.sectors)
        regimes = np.empty(_M, dtype=np.int8)
        r = start.regime
        for m in range(_M):
            if draws.u[m, 0] < self._p_switch[r]:
                others = [i for i in range(len(REGIMES)) if i != r]
                r = others[int(draws.u[m, 1] * len(others))]
            regimes[m] = r

        # Variance accumulated through each second (in units of a normal day's),
        # for the market factor and for the rest (sector and own noise). The move
        # into second r follows the regime of the minute before it ((r-1) // 60).
        by_second = np.repeat(regimes, 60)[: _S - 1]
        w_market = np.empty(_S)
        w_own = np.empty(_S)
        w_market[0] = self._gap_weight * self._market_mults[start.regime]
        w_own[0] = self._gap_weight * self._own_mults[start.regime]
        w_market[1:] = self._second_weight * self._market_mults[by_second]
        w_own[1:] = self._second_weight * self._own_mults[by_second]
        v_market = np.cumsum(w_market**2)
        v_own = np.cumsum(w_own**2)
        tilt = np.concatenate(([0.0], np.cumsum(self._tilt[by_second])))

        # Knots: each minute's first and last second, alternating.
        knots = np.empty(2 * _M, dtype=int)
        knots[0::2] = np.arange(_M) * 60
        knots[1::2] = knots[0::2] + 59

        # The unit paths (market, sectors, own) at the knots.
        steps = np.empty((2 * _M, 1 + k + n))
        own_cols = slice(1, None)
        for v, cols in ((v_market, slice(0, 1)), (v_own, own_cols)):
            dv = np.diff(v[knots])
            steps[0, cols] = math.sqrt(v[0]) * draws.gap[cols]
            steps[1::2, cols] = np.sqrt(dv[0::2])[:, None] * draws.within[:, cols]
            steps[2::2, cols] = np.sqrt(dv[1::2])[:, None] * draws.between[:-1, cols]
        paths = np.cumsum(steps, axis=0)

        # Each symbol's loadings on them, today.
        h = start.variance
        h_own = np.ones(n) if start.own_variance is None else start.own_variance
        market_load = self._rho * self._day_sd * math.sqrt(h)
        own_sd = np.sqrt(1 - self._rho**2) * self._day_sd * np.sqrt(h_own)
        sector_load = own_sd * self._sector_corr
        own_load = own_sd * np.sqrt(1 - self._sector_corr**2)

        # Log prices at the knots: the loaded paths, the drift (lognormal-corrected),
        # and the jumps.
        t = knots[:, None]
        log = (
            np.log(start.prev_close)
            + market_load * paths[:, :1]
            + sector_load * paths[:, 1 + self._sector_of]
            + own_load * paths[:, 1 + k :]
            + self._drift / (TRADING_DAYS_PER_YEAR * (_S - 1)) * t
            + self._beta * tilt[knots][:, None]
            - 0.5 * (market_load**2 * v_market[knots][:, None] + own_sd**2 * v_own[knots][:, None])
        )
        earnings_on = self._earnings_on(day)
        em = np.where(earnings_on, self._earnings_move, 0.0)
        own_jump = draws.earnings * em - 0.5 * em**2
        crash = 0.0
        if draws.crash_u < CRASHES_PER_YEAR / TRADING_DAYS_PER_YEAR:
            crash = CRASH_MIN + CRASH_EXTRA * draws.crash_size
        log += own_jump - crash * self._beta
        news = np.flatnonzero(draws.news_minute >= 0)
        for i in news:
            size = draws.news_size[i] * self._news_sd[i] - 0.5 * self._news_sd[i] ** 2
            log[2 * (draws.news_minute[i] + 1) :, i] += size
            own_jump[i] += size

        # Each minute's high and low, from the bridge between its open and close.
        open_, close = log[0::2], log[1::2]
        dv_market = v_market[knots[1::2]] - v_market[knots[0::2]]
        dv_own = v_own[knots[1::2]] - v_own[knots[0::2]]
        var = market_load**2 * dv_market[:, None] + own_sd**2 * dv_own[:, None]
        one_second = np.sqrt(var / 59)
        top, bottom = np.maximum(open_, close), np.minimum(open_, close)
        move = np.abs(close - open_)
        high = (open_ + close + np.sqrt(move**2 - 2 * var * np.log(draws.extremes[..., 0]))) / 2
        low = (open_ + close - np.sqrt(move**2 - 2 * var * np.log(draws.extremes[..., 1]))) / 2
        high -= DISCRETE_EXTREME_SHIFT * one_second
        low += DISCRETE_EXTREME_SHIFT * one_second
        # Within half a second's move of the open or close, the extreme is that price.
        high = np.where(high - top < 0.5 * one_second, top, high)
        low = np.where(bottom - low < 0.5 * one_second, bottom, low)

        # Volume: U-shaped through the day, busier in volatile regimes, on big moves,
        # after news and on earnings days.
        activity = ACTIVITY_BASE + (1 - ACTIVITY_BASE) * np.minimum(
            np.divide(move**2, var, out=np.ones_like(var), where=var > 0), ACTIVITY_CAP
        )
        for i in news:
            after = np.arange(_M) - (draws.news_minute[i] + 1)
            activity[:, i] *= 1 + NEWS_VOLUME_BURST * np.where(after >= 0, np.exp(-after / NEWS_VOLUME_DECAY), 0)
        noise = draws.volume_noise / math.exp(VOLUME_NOISE**2 / 2)  # mean 1
        level = self._daily_volume * math.sqrt(h) * np.where(earnings_on, EARNINGS_VOLUME, 1.0)
        shape = self._volume_shape * self._volume_mults[regimes]
        volume = np.floor(level / _M * shape[:, None] * activity * noise)
        cum_volume = np.zeros((_M + 1, n))
        cum_volume[1:] = np.cumsum(volume, axis=0)

        # Spreads: by the market's volatility this minute relative to normal.
        normal_second = math.sqrt((1 - GAP_SHARE) / (_S - 1))
        spread_factor = np.clip(np.sqrt(dv_market * h / 59) / normal_second, 1.0, MAX_SPREAD_FACTOR)

        # The day's shocks (a jump counts in full) set the next session's variances.
        market_day_sd = MARKET_VOL / math.sqrt(TRADING_DAYS_PER_YEAR) * math.sqrt(h)
        shock = paths[-1, 0] - crash / market_day_sd
        response = GARCH_ALPHA + (GARCH_GAMMA if shock < 0 else 0.0)
        h_next = float(np.clip(self._omega + GARCH_BETA * h + response * shock**2 * h, *VARIANCE_RANGE))
        own_shock = self._sector_corr * paths[-1, 1 + self._sector_of] + np.sqrt(1 - self._sector_corr**2) * paths[-1, 1 + k :]
        own_shock += np.divide(own_jump, own_sd, out=np.zeros(n), where=own_sd > 0)
        own_response = OWN_GARCH_ALPHA + np.where(own_shock < 0, OWN_GARCH_GAMMA, 0.0)
        h_own_next = np.where(
            own_sd > 0,
            np.clip(self._own_omega + OWN_GARCH_BETA * h_own + own_response * own_shock**2 * h_own, *VARIANCE_RANGE),
            1.0,
        )

        return _Skeleton(
            regimes=regimes,
            open=open_,
            high=high,
            low=low,
            close=close,
            volume=volume,
            cum_volume=cum_volume,
            spread=spread_factor,
            market_load=market_load,
            sector_load=sector_load,
            own_load=own_load,
            minute_seed=draws.minute_seed,
            # The close is the last price shown in the session (one second before 4:00).
            next_start=_DayStart(np.exp(close[-1]), int(regimes[-1]), h_next, h_own_next),
        )

    def _fine(self, day: date, m: int, cols: int | np.ndarray | None = None) -> np.ndarray:
        """(60, N) prices for each second of minute `m`, generated for `cols`
        (default: every symbol); other columns may not be filled in yet."""
        key = (day, m)
        if key in self._fines:
            self._fines.move_to_end(key)
            prices, done = self._fines[key]
        else:
            n = len(self.specs)
            prices, done = np.empty((60, n)), np.zeros(n, dtype=bool)
            self._cache(self._fines, key, (prices, done), FINE_CACHE_MINUTES, keep=key)
        want = np.arange(len(self.specs)) if cols is None else np.atleast_1d(cols)
        missing = want[~done[want]]
        if missing.size:
            prices[:, missing] = self._seconds(day, m, missing)
            done[missing] = True
        return prices

    def _seconds(self, day: date, m: int, cols: np.ndarray) -> np.ndarray:
        """(60, len(cols)) prices for each second of minute `m`: a bridge through
        the minute's open, high, low and close, shaped by the same market, sector
        and own noise as the rest (so symbols still move together second to second)."""
        sk = self._skeleton(day)
        k = len(self.sectors)
        seed, ordinal, reg = int(sk.minute_seed[m]), day.toordinal(), sk.regimes[m]
        weights = self._second_weight[60 * m : 60 * m + 59]  # rows 60m+1 .. 60m+59

        def walk(path: int, mult: float) -> np.ndarray:
            rng = np.random.default_rng([seed, ordinal, _FINE, m, self._keys[path]])
            out = np.zeros(60)
            out[1:] = np.cumsum(weights * mult * rng.standard_normal(59))
            return out

        market = walk(0, self._market_mults[reg])
        sectors: dict[int, np.ndarray] = {}
        out = np.empty((60, len(cols)))
        r = np.arange(60)
        inner = slice(1, 59)
        for c, i in enumerate(cols):
            noise = sk.market_load[i] * market + sk.own_load[i] * walk(1 + k + i, self._own_mults[reg])
            if sk.sector_load[i]:
                j = int(self._sector_of[i])
                if j not in sectors:
                    sectors[j] = walk(1 + j, self._own_mults[reg])
                noise += sk.sector_load[i] * sectors[j]
            x0, x1, hi, lo = sk.open[m, i], sk.close[m, i], sk.high[m, i], sk.low[m, i]
            # The high and low come where the noise (pinned at the open and close)
            # peaks and dips, so symbols that move together peak together.
            pinned = noise - np.interp(r, (0, 59), noise[[0, 59]])
            times, values = [0, 59], [x0, x1]
            t_hi = None
            if hi > max(x0, x1):
                t_hi = 1 + int(np.argmax(pinned[inner]))
                times.append(t_hi)
                values.append(hi)
            if lo < min(x0, x1):
                t_lo = 1 + int(np.argmin(pinned[inner]))
                if t_lo == t_hi:
                    t_lo = t_lo + 1 if t_lo < 58 else t_lo - 1
                times.append(t_lo)
                values.append(lo)
            order = np.argsort(times)
            times_s, values_s = np.array(times)[order], np.array(values)[order]
            # Pin the noise at every anchor first, so anchors come out exactly.
            path = np.interp(r, times_s, values_s) + (noise - np.interp(r, times_s, noise[times_s]))
            for _ in range(3):  # fold back inside the minute's range
                path = np.where(path > hi, 2 * hi - path, path)
                path = np.where(path < lo, 2 * lo - path, path)
            out[:, c] = np.clip(path, lo, hi)
        return np.exp(out)

    @staticmethod
    def _cache(cache: OrderedDict, key, value, size: int, keep) -> None:
        """Keep the `size` most recent entries, never evicting `keep` (the
        current session's skeleton: every tick reads it)."""
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > size:
            del cache[next(k for k in cache if k != keep)]

    def _forget(self, which: Callable[[date], bool]) -> None:
        """Drop cached sessions (and their seconds) for the days `which` selects."""
        for d in [d for d in self._skeletons if which(d)]:
            del self._skeletons[d]
        for key in [key for key in self._fines if which(key[0])]:
            del self._fines[key]

    def _earnings_on(self, day: date) -> np.ndarray:
        """(N,) whether each symbol reports earnings before this session."""
        on = np.zeros(len(self.specs), dtype=bool)
        for i, spec in enumerate(self.specs):
            if spec.earnings is None:
                continue
            month, dom = spec.earnings
            if (day.month - 1) % 3 != month:
                continue
            target = date(day.year, day.month, dom)
            on[i] = day == (target if is_trading_day(target) else next_trading_day(target))
        return on


def _key(name: str) -> int:
    return zlib.crc32(name.encode())


def _unit_hash(x: np.ndarray) -> np.ndarray:
    """Uniform numbers in (0, 1) from uint64s (splitmix64's mixer)."""
    with np.errstate(over="ignore"):
        x = x + np.uint64(0x9E3779B97F4A7C15)
        x = (x ^ (x >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
        x = (x ^ (x >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
        x = x ^ (x >> np.uint64(31))
    return ((x >> np.uint64(11)).astype(float) + 0.5) / 2.0**53


def _start_from(saved: list) -> _DayStart:
    prev_close, regime, *rest = saved
    variance = rest[0] if rest else 1.0
    own = rest[1] if len(rest) > 1 and rest[1] is not None else None
    return _DayStart(np.array(prev_close), regime, variance, None if own is None else np.array(own))


def _stationary(p_switch: list[float]) -> np.ndarray:
    """Long-run share of time in each regime, for a chain that leaves regime r
    with probability p_switch[r] per minute, to any other regime equally."""
    k = len(p_switch)
    if k == 1:
        return np.ones(1)
    p = np.array(p_switch)[:, None] / (k - 1) * np.ones((k, k))
    np.fill_diagonal(p, 1 - np.array(p_switch))
    a = p.T - np.eye(k)
    a[-1] = 1
    b = np.zeros(k)
    b[-1] = 1
    return np.linalg.solve(a, b)


def _intraday_shape() -> np.ndarray:
    """(S-1,) volatility multiplier for each second after the open, averaging 1 in variance."""
    x = (np.arange(_S - 1) + 0.5) / (_S - 1)
    v = 1 + OPEN_BURST * np.exp(-x / BURST_DECAY) + CLOSE_BURST * np.exp(-(1 - x) / BURST_DECAY)
    return v / math.sqrt(np.mean(v**2))
