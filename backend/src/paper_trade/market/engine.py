"""Synthetic price engine.

Prices exist only during trading sessions (see `calendar`). Each session's
path is generated in one go, second by second, from random draws seeded by
(seed, date). That makes every moment reproducible: the engine can jump to any
time, forward or back, and get exactly the same prices as if it had run there
tick by tick.

Model, per symbol: geometric Brownian motion driven by a shared market shock
(so symbols move together) plus its own noise, scaled by a market regime
(calm / normal / volatile) that switches minute by minute. Part of each day's
variance arrives as an overnight gap at the open.

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
    market_day,
    next_trading_day,
    previous_trading_day,
    session_close,
    session_open,
    session_seconds,
    snap,
)
from paper_trade.market.symbols import SYMBOLS, SymbolSpec

TRADING_DAYS_PER_YEAR = 252

# Share of a day's price variance that arrives as the overnight gap.
GAP_SHARE = 0.2

# (name, volatility multiplier, average market-hours spent in the regime)
REGIMES = (("calm", 0.6, 12.0), ("normal", 1.0, 24.0), ("volatile", 2.0, 4.0))

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


@dataclass
class _DayPath:
    prices: np.ndarray  # (S+1, N): row s is the price s seconds after the open (row S is unused)
    high: np.ndarray  # (S+1, N) running max of prices
    low: np.ndarray  # (S+1, N) running min of prices
    minute_volume: np.ndarray  # (M, N)
    volume: np.ndarray  # (M+1, N) cumulative volume at each minute boundary
    regimes: np.ndarray  # (M,) regime index for each minute


class MarketEngine:
    def __init__(self, start: datetime, seed: int = 42, specs: tuple[SymbolSpec, ...] = SYMBOLS) -> None:
        self.specs = specs
        self._vol = np.array([s.vol for s in specs])
        self._drift = np.array([s.drift for s in specs])
        self._rho = np.array([s.market_corr for s in specs])
        self._daily_volume = np.array([s.daily_volume for s in specs], dtype=float)
        self._p_switch = [1 - math.exp(-1 / (hours * 60)) for _, _, hours in REGIMES]
        self._mults = np.array([m for _, m, _ in REGIMES])
        self._seed = seed
        self._seeds_used = 0
        # Called with each session as the engine moves past it (its path is final then).
        self.on_day_complete: Callable[[date], None] | None = None
        self._reset(start, seed)

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
            # The close is the last price shown in the session (one second before 4:00).
            self._starts[nxt] = _DayStart(path.prices[_S - 1].copy(), int(path.regimes[-1]))
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
        """Generate `days` sessions of history before the origin, newest first,
        yielding each finished session (its path is cached, ready to read).

        Each day's path is generated from a start of 1.0 and then scaled so its
        close equals the next day's previous close. Paths are multiplicative, so
        that is exactly the path a forward replay from the scaled start produces.

        Nothing is committed until the end: the origin and session starts change
        all at once, so an interrupted build leaves the market as it was (and
        saving mid-build stays consistent).
        """
        normal = next(i for i, (name, _, _) in enumerate(REGIMES) if name == "normal")
        pending: dict[date, _DayStart] = {}
        day = market_day(self.origin)
        for _ in range(days):
            prev = previous_trading_day(day)
            target = (pending.get(day) or self._starts[day]).prev_close
            # Generate the unit path with a temporary start, then take it back out.
            self._starts[prev] = _DayStart(np.ones(len(self.specs)), normal)
            self._paths.pop(prev, None)
            try:
                unit = self._path(prev)
            finally:
                del self._starts[prev]
            scale = target / unit.prices[_S - 1]
            pending[prev] = _DayStart(scale, normal)
            self._cache_path(
                prev,
                _DayPath(
                    prices=unit.prices * scale,
                    high=unit.high * scale,
                    low=unit.low * scale,
                    minute_volume=unit.minute_volume,
                    volume=unit.volume,
                    regimes=unit.regimes,
                ),
            )
            day = prev
            yield prev
        self._starts.update(pending)
        if pending:
            self.origin = session_open(day)
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
            "starts": {d.isoformat(): [s.prev_close.tolist(), s.regime] for d, s in self._starts.items()},
            "seed": self._seed,
            "seeds_used": self._seeds_used,
            "backfilled": self.backfilled,
        }

    @classmethod
    def from_state(cls, state: dict, specs: tuple[SymbolSpec, ...] = SYMBOLS) -> "MarketEngine":
        engine = cls(datetime.fromisoformat(state["origin"]), seed=state["seed"], specs=specs)
        engine._seeds_used = state["seeds_used"]
        engine.backfilled = state.get("backfilled", False)
        engine._time = datetime.fromisoformat(state["time"])
        engine._epochs = [(datetime.fromisoformat(start), seed) for start, seed in state["epochs"]]
        engine._starts = {
            date.fromisoformat(d): _DayStart(np.array(prev_close), regime)
            for d, (prev_close, regime) in state["starts"].items()
        }
        return engine

    # ----- internals ---------------------------------------------------------

    def _reset(self, start: datetime, seed: int) -> None:
        self.origin = snap(start)
        self._time = self.origin
        self._epochs: list[tuple[datetime, int]] = [(self.origin, seed)]
        normal = next(i for i, (name, _, _) in enumerate(REGIMES) if name == "normal")
        self._starts: dict[date, _DayStart] = {
            market_day(self.origin): _DayStart(np.array([s.price for s in self.specs]), normal)
        }
        self._paths: OrderedDict[date, _DayPath] = OrderedDict()
        self.backfilled = False
        # Bumped whenever session start states change, so savers can skip the large part.
        self.starts_version = getattr(self, "starts_version", 0) + 1

    def _new_seed(self) -> int:
        self._seeds_used += 1
        return (self._seed * 1_000_003 + self._seeds_used) % 2**63

    def _random(self, seed: int, day: date) -> tuple[np.ndarray, ...]:
        n = len(self.specs)
        rng = np.random.default_rng([seed, day.toordinal()])
        z_market = rng.standard_normal(_S + 1)
        z_own = rng.standard_normal((_S + 1, n))
        u = rng.random((_M, 2))
        volume_noise = rng.lognormal(0.0, VOLUME_NOISE, (_M, n))
        return z_market, z_own, u, volume_noise

    def _draws(self, day: date) -> tuple[np.ndarray, ...]:
        """The day's random draws, honoring any epochs that start during it.

        Row s of the shocks is the move that ends s seconds after the open (row 0
        is the overnight gap). A new epoch starting at second c replaces rows
        after c, and minutes that start at or after c.
        """
        open_, close = session_open(day), session_close(day)
        before = [e for e in self._epochs if e[0] < open_]
        base = before[-1] if before else self._epochs[0]
        z_market, z_own, u, volume_noise = self._random(base[1], day)
        for epoch in self._epochs:
            start, seed = epoch
            if epoch is base or not open_ <= start < close:
                continue
            cut = session_seconds(start)
            row = math.floor(cut) + 1
            minute = math.ceil(cut / 60)
            new = self._random(seed, day)
            z_market[row:] = new[0][row:]
            z_own[row:] = new[1][row:]
            u[minute:] = new[2][minute:]
            volume_noise[minute:] = new[3][minute:]
        return z_market, z_own, u, volume_noise

    def _path(self, day: date) -> _DayPath:
        if day in self._paths:
            self._paths.move_to_end(day)
            return self._paths[day]

        start = self._starts[day]
        z_market, z_own, u, volume_noise = self._draws(day)

        regimes = np.empty(_M, dtype=np.int8)
        r = start.regime
        for m in range(_M):
            if u[m, 0] < self._p_switch[r]:
                others = [i for i in range(len(REGIMES)) if i != r]
                r = others[int(u[m, 1] * len(others))]
            regimes[m] = r
        mult = self._mults[regimes]

        # Split each day's variance between the overnight gap and the session.
        day_var = self._vol**2 / TRADING_DAYS_PER_YEAR
        sd = np.empty((_S + 1, len(self.specs)))
        sd[0] = np.sqrt(day_var * GAP_SHARE) * self._mults[start.regime]
        sd[1:] = np.sqrt(day_var * (1 - GAP_SHARE) / _S) * np.repeat(mult, 60)[:, None]

        z = self._rho * z_market[:, None] + np.sqrt(1 - self._rho**2) * z_own
        log_returns = z * sd - 0.5 * sd**2
        log_returns[1:] += self._drift / (TRADING_DAYS_PER_YEAR * _S)
        prices = start.prev_close * np.exp(np.cumsum(log_returns, axis=0))

        # U-shaped intraday volume: busiest at the open and the close.
        x = (np.arange(_M) + 0.5) / _M
        shape = 1 + 2 * (2 * x - 1) ** 2
        shape /= shape.mean()
        noise = volume_noise / math.exp(VOLUME_NOISE**2 / 2)  # mean 1
        minute_volume = np.floor(self._daily_volume / _M * shape[:, None] * mult[:, None] * noise)
        volume = np.zeros((_M + 1, len(self.specs)))
        volume[1:] = np.cumsum(minute_volume, axis=0)

        path = _DayPath(
            prices=prices,
            high=np.maximum.accumulate(prices, axis=0),
            low=np.minimum.accumulate(prices, axis=0),
            minute_volume=minute_volume,
            volume=volume,
            regimes=regimes,
        )
        self._cache_path(day, path)
        return path

    def _cache_path(self, day: date, path: _DayPath) -> None:
        """Keep a few recent paths, never evicting the current session's (every tick reads it)."""
        self._paths[day] = path
        self._paths.move_to_end(day)
        current = market_day(self._time)
        while len(self._paths) > PATH_CACHE_DAYS:
            oldest = next(d for d in self._paths if d != current)
            del self._paths[oldest]
