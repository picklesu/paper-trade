"""Synthetic price engine.

Prices exist only during trading sessions (see `calendar`). Each session's
path is generated in one go, second by second, from random draws seeded by
(seed, date). That makes every moment reproducible: the engine can jump to any
time, forward or back, and get exactly the same prices as if it had run there
tick by tick.

Model, per symbol: a shared market factor (so symbols move together) plus the
symbol's own noise, with:
- a market regime (calm / normal / volatile) that switches minute by minute,
  scaling volatility (the market factor's more than each symbol's own, so stocks
  move together more in turbulent markets) and leaning the drift;
- volatility clustering with the leverage effect: the market factor's daily
  variance follows GJR-GARCH(1,1), so a big day (a drop especially) is followed
  by turbulent days that calm down gradually;
- fat tails: part of each day's variance arrives as an overnight gap drawn from
  Student's t, plus earnings gaps on a quarterly schedule, occasional intraday
  news jumps per symbol, and rare market-wide selloffs at the open;
- intraday volatility highest just after the open and raised into the close.
Each symbol's long-run volatility and drift still match its spec: jumps take
their share of the variance from the continuous part.

Going back in time either replays the same future or starts a new one. A new
future is a new "epoch": random draws after its start time come from a new
seed, while everything before it is unchanged.

Each market also gets years of history before its start (`backfill_iter`),
generated backward so that it ends exactly at the starting prices.
"""

import math
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
# News: about NEWS_PER_YEAR times a year a symbol jumps intraday, with a standard
# deviation of NEWS_SIZE days of its volatility.
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

# GJR-GARCH(1,1) on the market factor's daily variance, as a multiple of normal
# (averaging 1). GAMMA is the extra response to down days.
GARCH_ALPHA, GARCH_GAMMA, GARCH_BETA = 0.02, 0.16, 0.88
VARIANCE_RANGE = (0.25, 8.0)

# Intraday volatility: OPEN_BURST and CLOSE_BURST extra at the open and close,
# fading over BURST_DECAY of the session (about 12 minutes).
OPEN_BURST, CLOSE_BURST, BURST_DECAY = 1.5, 0.5, 0.03

# Saved markets record the model that generated them; prices from another model
# can't be regenerated (see `MarketEngine.continue_older`).
MODEL_VERSION = 2

VOLUME_NOISE = 0.5  # lognormal sigma of per-minute volume
HISTORY_DAYS = TRADING_DAYS_PER_YEAR * 5  # history generated before a market's start
PATH_CACHE_DAYS = 4

_S = SESSION_SECONDS
_M = SESSION_MINUTES


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
        }


def bid_ask(price: float) -> tuple[float, float]:
    """Penny-wide spread for most prices, about 1bp for expensive ones."""
    spread = max(0.01, round(price * 0.0001, 2))
    # Round down to the cent; round() first so float noise like 57996.99999 floors correctly.
    bid = math.floor(round((price - spread / 2) * 100, 6)) / 100
    ask = round(bid + spread, 2)
    if ask < price:  # e.g. 1.234 floors to 1.22/1.23; shift up so the quote brackets the price
        bid, ask = round(bid + 0.01, 2), round(ask + 0.01, 2)
    return bid, ask


def bid_ask_arrays(prices: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """`bid_ask` for an array of prices, with identical results."""
    spread = np.maximum(0.01, np.round(prices * 0.0001, 2))
    bid = np.floor(np.round((prices - spread / 2) * 100, 6)) / 100
    ask = np.round(bid + spread, 2)
    low = ask < prices
    bid = np.where(low, np.round(bid + 0.01, 2), bid)
    ask = np.where(low, np.round(ask + 0.01, 2), ask)
    return bid, ask


@dataclass
class _DayStart:
    prev_close: np.ndarray  # (N,) previous session's closing prices
    regime: int  # regime at the end of the previous session
    variance: float = 1.0  # the market factor's variance for the session, as a multiple of normal


@dataclass
class _Draws:
    """A session's random draws. Row s of the shocks is the move that ends s
    seconds after the open (row 0 is the overnight gap)."""

    z_market: np.ndarray  # (S+1,)
    z_own: np.ndarray  # (S+1, N)
    u: np.ndarray  # (M, 2) regime switching
    volume_noise: np.ndarray  # (M, N)
    news_at: np.ndarray  # (N,) the row where news arrives for each symbol, or 0 for none
    news_size: np.ndarray  # (N,) unit-variance news jump sizes
    earnings: np.ndarray  # (N,) unit-variance earnings moves
    crash_u: float  # a selloff happens if this uniform is small enough
    crash_size: float  # unit exponential


@dataclass
class _DayPath:
    prices: np.ndarray  # (S+1, N): row s is the price s seconds after the open (row S is unused)
    high: np.ndarray  # (S+1, N) running max of prices
    low: np.ndarray  # (S+1, N) running min of prices
    minute_volume: np.ndarray  # (M, N)
    volume: np.ndarray  # (M+1, N) cumulative volume at each minute boundary
    regimes: np.ndarray  # (M,) regime index for each minute
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

        # Jumps take their share of each symbol's variance from the continuous part.
        crash_mean = CRASH_MIN + CRASH_EXTRA
        jump_var = (
            EARNINGS_PER_YEAR * self._earnings_move**2
            + NEWS_PER_YEAR * self._news_sd**2
            + CRASHES_PER_YEAR * (crash_mean**2 + CRASH_EXTRA**2) * self._beta**2
        )
        self._day_sd = np.sqrt(np.maximum(vol**2 - jump_var, 0.5 * vol**2) / TRADING_DAYS_PER_YEAR)
        # Selloffs would pull the drift down; add their average back.
        self._drift = np.array([s.drift for s in specs]) + CRASHES_PER_YEAR * crash_mean * self._beta
        # GARCH's constant, so the variance averages 1. Selloffs add to the squared
        # shocks (on average this much a day, in units of a normal day's variance).
        crash_shock = CRASHES_PER_YEAR * (crash_mean**2 + CRASH_EXTRA**2) / MARKET_VOL**2
        self._omega = (
            1 - GARCH_BETA - GARCH_ALPHA - GARCH_GAMMA / 2 - (GARCH_ALPHA + GARCH_GAMMA) * crash_shock
        )

        self._p_switch = [1 - math.exp(-1 / (r[2] * 60)) for r in REGIMES]
        self._normal = next(i for i, r in enumerate(REGIMES) if r[0] == "normal")
        share = _stationary(self._p_switch)  # long-run share of time in each regime
        mults = np.array([r[1] for r in REGIMES])
        self._volume_mults = mults / (share @ mults)
        self._market_mults = mults / math.sqrt(share @ mults**2)
        self._own_mults = np.sqrt(mults) / math.sqrt(share @ mults)
        tilt = np.array([r[3] for r in REGIMES])
        self._tilt = tilt - share @ tilt
        self._shape = _intraday_shape()

        self._seed = seed
        self._seeds_used = 0
        # Called with each session as the engine moves past it (its path is final then).
        self.on_day_complete: Callable[[date], None] | None = None
        # Set by a finished backfill: what its unit-start paths were multiplied by.
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
            path = self._path(last)
            nxt = next_trading_day(last)
            self._starts[nxt] = path.next_start
            self.starts_version += 1
            if self.on_day_complete:
                self.on_day_complete(last)
            last = nxt
            yield last
        self._time = t

    def rewind(self, t: datetime, new_future: bool) -> None:
        """Go back to `t`. Prices up to `t` are unchanged; after it they either
        replay exactly or, with `new_future`, are generated from a new seed."""
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
            for d in [d for d in self._paths if d >= day]:
                del self._paths[d]
        self._time = t

    def reset(self, t: datetime) -> None:
        """Start a brand-new market at `t`, with fresh prices and no history."""
        self._reset(t, self._new_seed())

    # ----- current state -----------------------------------------------------

    @property
    def quotes(self) -> dict[str, Quote]:
        day = market_day(self._time)
        path = self._path(day)
        s = min(int(session_seconds(self._time)), _S - 1)
        m, sec = divmod(s, 60)
        volume = path.volume[m] + path.minute_volume[m] * sec / 60
        prev_close = self._starts[day].prev_close
        quotes = {}
        for i, spec in enumerate(self.specs):
            last = float(path.prices[s, i])
            bid, ask = bid_ask(last)
            quotes[spec.symbol] = Quote(
                symbol=spec.symbol,
                name=spec.name,
                last=last,
                bid=bid,
                ask=ask,
                open=float(path.prices[0, i]),
                high=float(path.high[s, i]),
                low=float(path.low[s, i]),
                prev_close=float(prev_close[i]),
                volume=int(volume[i]),
            )
        return quotes

    @property
    def regime(self) -> str:
        """The regime that drove the latest price move (the gap's at the open)."""
        day = market_day(self._time)
        s = min(int(session_seconds(self._time)), _S - 1)
        r = self._starts[day].regime if s == 0 else self._path(day).regimes[(s - 1) // 60]
        return REGIMES[r][0]

    def backfill_iter(self, days: int = HISTORY_DAYS) -> Iterator[date]:
        """Generate `days` sessions of history before the origin, oldest first,
        yielding each one with its path cached at unit scale (see below).

        The history runs forward, so volatility clustering and regimes carry
        from day to day, starting from prices of 1.0. Paths are multiplicative,
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
                path = self._generate(day, start, self._draws(day))
                self._cache_path(day, path)
                start = path.next_start
                yield day
                day = next_trading_day(day)
        finally:
            # The unit-scale paths must never be read as prices, finished or not.
            for d in [d for d in self._paths if d < origin_day]:
                del self._paths[d]
        scale = self._starts[origin_day].prev_close / start.prev_close
        self._starts.update({d: _DayStart(s.prev_close * scale, s.regime, s.variance) for d, s in unit.items()})
        self.backfill_scale = scale
        if unit:
            self.origin = session_open(min(unit))
            self.starts_version += 1
        self.backfilled = True

    def minute_bars(self, day: date, through: int | None = None) -> np.ndarray:
        """(minutes, N, 5) one-minute [open, high, low, close, volume] bars for a
        session, for the whole day or only through second `through`."""
        path = self._path(day)
        n = len(self.specs)
        p = path.prices[:_S].reshape(_M, 60, n)
        bars = np.empty((_M, n, 5))
        bars[..., 0] = p[:, 0]
        bars[..., 1] = p.max(axis=1)
        bars[..., 2] = p.min(axis=1)
        bars[..., 3] = p[:, -1]
        bars[..., 4] = path.minute_volume
        if through is None:
            return bars
        m, sec = divmod(min(through, _S - 1), 60)
        bars = bars[: m + 1].copy()
        part = path.prices[60 * m : 60 * m + sec + 1]
        bars[m, :, 0] = part[0]
        bars[m, :, 1] = part.max(axis=0)
        bars[m, :, 2] = part.min(axis=0)
        bars[m, :, 3] = part[-1]
        bars[m, :, 4] = path.minute_volume[m] * sec / 60
        return bars

    def day_prices(self, day: date) -> np.ndarray:
        """(S, N) per-second prices for a session already reached; row s is s
        seconds after the open. Read-only."""
        return self._path(day).prices[:_S]

    def prices_at(self, t: datetime) -> dict[str, float]:
        """Last prices at a past market time (between origin and now)."""
        t = snap(t)
        row = self.day_prices(market_day(t))[min(int(session_seconds(t)), _S - 1)]
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
                d.isoformat(): [s.prev_close.tolist(), s.regime, s.variance] for d, s in self._starts.items()
            },
            "seed": self._seed,
            "seeds_used": self._seeds_used,
            "backfilled": self.backfilled,
            "model": MODEL_VERSION,
        }

    @classmethod
    def from_state(cls, state: dict, specs: tuple[SymbolSpec, ...] = SYMBOLS) -> "MarketEngine":
        engine = cls(datetime.fromisoformat(state["origin"]), seed=state["seed"], specs=specs)
        engine._seeds_used = state["seeds_used"]
        engine.backfilled = state.get("backfilled", False)
        engine._time = datetime.fromisoformat(state["time"])
        engine._epochs = [(datetime.fromisoformat(start), seed) for start, seed in state["epochs"]]
        engine._starts = {
            date.fromisoformat(d): _DayStart(np.array(start[0]), start[1], *start[2:])
            for d, start in state["starts"].items()
        }
        return engine

    @classmethod
    def continue_older(cls, state: dict, specs: tuple[SymbolSpec, ...] = SYMBOLS) -> "MarketEngine":
        """A market saved by an older price model can't be regenerated, so start a
        new one at its time, from that session's previous closes. Its history is
        then generated again (`backfilled` is false)."""
        t = datetime.fromisoformat(state["time"])
        prev_close = state["starts"][market_day(t).isoformat()][0]
        engine = cls(t, seed=state["seed"], specs=specs, prices=np.array(prev_close))
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
        self._paths: OrderedDict[date, _DayPath] = OrderedDict()
        self.backfilled = False
        # Bumped whenever session start states change, so savers can skip the large part.
        self.starts_version = getattr(self, "starts_version", 0) + 1

    def _new_seed(self) -> int:
        self._seeds_used += 1
        return (self._seed * 1_000_003 + self._seeds_used) % 2**63

    def _random(self, seed: int, day: date) -> _Draws:
        n = len(self.specs)
        rng = np.random.default_rng([seed, day.toordinal()])
        z_market = rng.standard_normal(_S + 1)
        z_own = rng.standard_normal((_S + 1, n))
        u = rng.random((_M, 2))
        volume_noise = rng.lognormal(0.0, VOLUME_NOISE, (_M, n))
        # Fat-tailed overnight gaps.
        gap_scale = math.sqrt((GAP_DF - 2) / GAP_DF)
        z_market[0] = rng.standard_t(GAP_DF) * gap_scale
        z_own[0] = rng.standard_t(GAP_DF, n) * gap_scale
        jump_scale = math.sqrt((JUMP_DF - 2) / JUMP_DF)
        hit = rng.random(n) < NEWS_PER_YEAR / TRADING_DAYS_PER_YEAR
        news_at = np.where(hit, rng.integers(1, _S, n), 0)
        news_size = rng.standard_t(JUMP_DF, n) * jump_scale
        earnings = rng.standard_t(JUMP_DF, n) * jump_scale
        crash_u, crash_size = rng.random(), rng.exponential()
        return _Draws(z_market, z_own, u, volume_noise, news_at, news_size, earnings, crash_u, crash_size)

    def _draws(self, day: date) -> _Draws:
        """The day's random draws, honoring any epochs that start during it.

        A new epoch starting at second c replaces the shock rows after c, and
        minutes that start at or after c. The gap's draws (earnings, selloffs)
        always come from the epoch in force at the open.
        """
        open_, close = session_open(day), session_close(day)
        before = [e for e in self._epochs if e[0] < open_]
        base = before[-1] if before else self._epochs[0]
        draws = self._random(base[1], day)
        for epoch in self._epochs:
            start, seed = epoch
            if epoch is base or not open_ <= start < close:
                continue
            cut = session_seconds(start)
            row = math.floor(cut) + 1
            minute = math.ceil(cut / 60)
            new = self._random(seed, day)
            draws.z_market[row:] = new.z_market[row:]
            draws.z_own[row:] = new.z_own[row:]
            # News before the cut stays; after it, the new epoch's (one per symbol a day).
            keep = (draws.news_at > 0) & (draws.news_at < row)
            draws.news_at = np.where(keep, draws.news_at, np.where(new.news_at >= row, new.news_at, 0))
            draws.news_size = np.where(keep, draws.news_size, new.news_size)
            draws.u[minute:] = new.u[minute:]
            draws.volume_noise[minute:] = new.volume_noise[minute:]
        return draws

    def _path(self, day: date) -> _DayPath:
        if day in self._paths:
            self._paths.move_to_end(day)
            return self._paths[day]
        path = self._generate(day, self._starts[day], self._draws(day))
        self._cache_path(day, path)
        return path

    def _generate(self, day: date, start: _DayStart, draws: _Draws) -> _DayPath:
        n = len(self.specs)
        regimes = np.empty(_M, dtype=np.int8)
        r = start.regime
        for m in range(_M):
            if draws.u[m, 0] < self._p_switch[r]:
                others = [i for i in range(len(REGIMES)) if i != r]
                r = others[int(draws.u[m, 1] * len(others))]
            regimes[m] = r
        by_second = np.repeat(regimes, 60)

        # Each row's share of a normal day's standard deviation, for the market
        # factor and for each symbol's own noise: the gap's share at the open,
        # then the session's, shaped through the day and scaled by the regime.
        gap, session = math.sqrt(GAP_SHARE), math.sqrt((1 - GAP_SHARE) / _S)
        market = np.empty(_S + 1)
        own = np.empty(_S + 1)
        market[0] = gap * self._market_mults[start.regime]
        own[0] = gap * self._own_mults[start.regime]
        market[1:] = session * self._shape * self._market_mults[by_second]
        own[1:] = session * self._shape * self._own_mults[by_second]

        h = start.variance
        market_sd = (math.sqrt(h) * market)[:, None] * (self._rho * self._day_sd)
        own_sd = own[:, None] * (np.sqrt(1 - self._rho**2) * self._day_sd)
        log_returns = market_sd * draws.z_market[:, None] + own_sd * draws.z_own - 0.5 * (market_sd**2 + own_sd**2)
        log_returns[1:] += (self._drift + self._tilt[by_second][:, None] * self._beta) / (TRADING_DAYS_PER_YEAR * _S)

        # Jumps, each lognormal-corrected like the rest so it doesn't bias the drift.
        cols = np.flatnonzero(draws.news_at)
        sd = self._news_sd[cols]
        log_returns[draws.news_at[cols], cols] += draws.news_size[cols] * sd - 0.5 * sd**2
        earnings = self._earnings_on(day)
        em = np.where(earnings, self._earnings_move, 0.0)
        log_returns[0] += draws.earnings * em - 0.5 * em**2
        crash = 0.0
        if draws.crash_u < CRASHES_PER_YEAR / TRADING_DAYS_PER_YEAR:
            crash = CRASH_MIN + CRASH_EXTRA * draws.crash_size
            log_returns[0] -= crash * self._beta
        prices = start.prev_close * np.exp(np.cumsum(log_returns, axis=0))

        # The day's market shock in units of the session's expected volatility
        # (a selloff counts in full) sets the next session's variance.
        market_day_sd = MARKET_VOL / math.sqrt(TRADING_DAYS_PER_YEAR) * math.sqrt(h)
        shock = market[:_S] @ draws.z_market[:_S] - crash / market_day_sd
        response = GARCH_ALPHA + (GARCH_GAMMA if shock < 0 else 0.0)
        h_next = float(np.clip(self._omega + GARCH_BETA * h + response * shock**2 * h, *VARIANCE_RANGE))

        # U-shaped intraday volume: busiest at the open and the close.
        x = (np.arange(_M) + 0.5) / _M
        shape = 1 + 2 * (2 * x - 1) ** 2
        shape /= shape.mean()
        noise = draws.volume_noise / math.exp(VOLUME_NOISE**2 / 2)  # mean 1
        level = self._daily_volume * math.sqrt(h) * np.where(earnings, EARNINGS_VOLUME, 1.0)
        minute_volume = np.floor(level / _M * shape[:, None] * self._volume_mults[regimes][:, None] * noise)
        volume = np.zeros((_M + 1, n))
        volume[1:] = np.cumsum(minute_volume, axis=0)

        return _DayPath(
            prices=prices,
            high=np.maximum.accumulate(prices, axis=0),
            low=np.minimum.accumulate(prices, axis=0),
            minute_volume=minute_volume,
            volume=volume,
            regimes=regimes,
            # The close is the last price shown in the session (one second before 4:00).
            next_start=_DayStart(prices[_S - 1].copy(), int(regimes[-1]), h_next),
        )

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

    def _cache_path(self, day: date, path: _DayPath) -> None:
        """Keep a few recent paths, never evicting the current session's (every tick reads it)."""
        self._paths[day] = path
        self._paths.move_to_end(day)
        current = market_day(self._time)
        while len(self._paths) > PATH_CACHE_DAYS:
            oldest = next(d for d in self._paths if d != current)
            del self._paths[oldest]


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
    """(S,) volatility multiplier for each second of the session, averaging 1 in variance."""
    x = (np.arange(_S) + 0.5) / _S
    v = 1 + OPEN_BURST * np.exp(-x / BURST_DECAY) + CLOSE_BURST * np.exp(-(1 - x) / BURST_DECAY)
    return v / math.sqrt(np.mean(v**2))
