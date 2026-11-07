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

import functools
import json
import math
import zlib
from collections import OrderedDict
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np

from paper_trade.market.calendar import (
    SESSION_MINUTES,
    SESSION_SECONDS,
    add_trading_days,
    day_effects,
    release_effects,
    is_trading_day,
    market_day,
    next_trading_day,
    previous_trading_day,
    session_close,
    session_minutes,
    session_open,
    session_seconds,
    snap,
    volume_events,
)
from paper_trade.market.realbars import RealBars, RealBarsGone, RealDay
from paper_trade.market.regime import STATES, RegimeParams, load_params
from paper_trade.market.symbols import SYMBOLS, SymbolSpec
from paper_trade.market.volume_profile import VolumeProfiles

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
CRASHES_PER_YEAR = 1.0  # on average; the regime scales it (CRASH_BY_STATE)
STATE_LEVEL_ADJ = (0.95, 0.95, 2.4)  # measured over seeds x 30 years (scripts/check_engine_states.py): a crisis is short, so its variance falls short of the fitted level; 2.4 puts its weekly volatility at 51% (history 52%; 1.8 gave 43-47%)
CRASH_BY_STATE = (0.3, 1.2, 4.0)  # selloff odds relative to the average in calm / choppy / crisis
CRASH_MIN = 0.015
CRASH_EXTRA = 0.015
# Uncertainty shocks (Bloom 2009): volatility jumps by a multiple of its normal level
# and fades with a half-life of about three months; the market drops at the onset
# (waiting and seeing) and rebounds a little as the uncertainty clears.
VIX_REGIME_WEIGHT = 0.25  # how much of the volatility regime in force shows in the index (it lasts hours)
VIX_STEP_MINUTES = 30  # the volatility index is sampled every half hour (the stored bar size)
SHOCKS_PER_YEAR = 0.0  # random uncertainty shocks are now the regime model's crisis entries; the Trigger button still works
SHOCK_EXCESS_MIN = 0.4  # the extra volatility at onset (0.65: about 1.65 times normal), between this
SHOCK_EXCESS_RANGE = 0.5  # and this much more (it stacks with the regime and GARCH, so the index can reach 100)
SHOCK_HALF_LIFE_DAYS = 60.0
SHOCK_VISIBLE = 0.13  # the top bar shows a shock while its extra volatility exceeds this: a typical week's change in volatility from other factors (measured within a state)
SHOCK_IN_CRISIS = 0.4  # a triggered shock's extra volatility is this share of the usual (the crisis it starts carries the rest)
# Market-wide circuit breakers (NYSE Rule 80B), on SPY's fall from the previous close: a 15-minute
# halt at 7% and at 13% (not after 3:25 PM), and no more trading that day at 20%.
BREAKER_LEVELS = (0.07, 0.13, 0.20)
HALT_MINUTES = 15
# The market-wide halts that really happened under today's rule (level reached). SPY's own prints can fall 7% on days the
# S&P 500 didn't (2015-08-24 -7.7%, 2025-04-10 -7.2%), so a real day is marked only if it is listed (all four lows here do cross -7%,
# checked on the stored bars); the value is the highest level that day reached. Add a day here if a new real halt follows an update.
# (Before 2013 halts keyed off the Dow at 10/20/30%, and none ever tripped.)
REAL_HALTS = {date(2020, 3, 9): 1, date(2020, 3, 12): 1, date(2020, 3, 16): 1, date(2020, 3, 18): 1}
BREAKER_LAST_MINUTE = 355  # a level 1 or 2 halt can't begin once the session is this many minutes old (3:25 PM)
SHOCK_DROP = 0.025  # the market's log drop at the open, per unit of excess
SHOCK_REBOUND = 0.06  # annual drift per unit of excess, while it lasts
_SHOCK_DECAY = 0.5 ** (1 / SHOCK_HALF_LIFE_DAYS)  # a day's decay of the excess
_SHOCK_MEAN = SHOCK_EXCESS_MIN + SHOCK_EXCESS_RANGE / 2
# On average a session's volatility is this many times higher because of shocks (the
# baseline is divided by it, so long-run volatility still matches each spec), and
# the market's drift this much lower (added back).
_SHOCK_DAILY_RATE = SHOCKS_PER_YEAR / TRADING_DAYS_PER_YEAR
SHOCK_VARIANCE = 1 + _SHOCK_DAILY_RATE * (
    2 * _SHOCK_MEAN / (1 - _SHOCK_DECAY) + (_SHOCK_MEAN**2 + SHOCK_EXCESS_RANGE**2 / 12) / (1 - _SHOCK_DECAY**2)
)
SHOCK_DRIFT_LOSS = SHOCKS_PER_YEAR * SHOCK_DROP * _SHOCK_MEAN - SHOCK_REBOUND * _SHOCK_DAILY_RATE * _SHOCK_MEAN / (1 - _SHOCK_DECAY)
VIX_BASE2 = 70.0  # the sim's index squared is about this plus VIX_SLOPE times the market variance multiple
VIX_SLOPE = 295.0  # (measured over seeds; real days set the multiple from the real VIX, so the sim index follows)
VIX_BIAS = 1.106  # the sim index ran this much above the real one over 2007-2012 once set from it (the day's own move adds to it)
EARNINGS_PER_YEAR = 4
# Volume follows the market's variance more gently than volatility does: real SPY trades about
# 50-60M in quiet markets and 150M+ in crises. With the regime model's calm state at a third of
# average variance, the old square-root link left calm days at about a third of normal volume.
VOLUME_ELASTICITY = 0.12
EARLY_CLOSE_VOLUME = 0.46  # an early close's volume as a share of a normal day's (median of 20 years of the 40 symbols; scripts/check_holidays.py)
VOLUME_LEVEL = 1.12  # puts the long-run average volume at about each spec's daily volume (measured over 6 seeds x 20 years; 1.00 again with the day-level volume factors, 8 seeds x 25 years)
EARNINGS_VOLUME = 2.5  # volume multiple on a symbol's earnings day

# GJR-GARCH(1,1) on daily variance, as a multiple of normal (averaging 1). GAMMA is
# the extra response to down days. The market factor's, then each symbol's own.
GARCH_ALPHA, GARCH_GAMMA, GARCH_BETA = 0.02, 0.16, 0.88
OWN_GARCH_ALPHA, OWN_GARCH_GAMMA, OWN_GARCH_BETA = 0.05, 0.05, 0.88
# A safety net, rarely reached: a lower cap would clip the big shocks GARCH needs
# to average 1, and bias volatility low.
VARIANCE_RANGE = (0.03, 25.0)

# Intraday volatility: OPEN_BURST and CLOSE_BURST extra at the open and close,
# fading over BURST_DECAY of the session (about 12 minutes).
OPEN_BURST, CLOSE_BURST, BURST_DECAY = 1.5, 0.5, 0.03

VOLUME_NOISE = 0.5  # lognormal sigma of per-minute volume (an AR(1) in the log: each symbol's fitted persistence)
VOLUME_BURST = 0.04  # chance a minute's volume bursts (news, program trades)
VOLUME_BURST_SIZE = 4.0  # a burst adds this many times the minute's usual volume on average
# Order flow comes first and the price follows (it used to be the other way round): a day's activity (the market's
# busy and quiet days, VOLUME_MARKET_SD, and the symbol's own, VOLUME_OWN_SD; each a fast and a slow AR(1) and a white
# part, so it clumps over days) sets how much trades and, through VOLUME_VOL_LINK, how volatile the day is; a signed
# imbalance (buyers against sellers) moves the price through the day in proportion to the volume that trades
# (FLOW_SHARE of the move) and makes a lopsided day busier, more so when sellers win; within the day the minute volume
# surprises drive the minute variance (the symbol's own noise, and the market's and sectors' through the shared
# market swings). All of it keeps each day's variance as it was. Tuned with scripts/check_volume.py to 5 years of the
# 40 symbols; a Friday-busiest weekday pattern.
VOLUME_FAST = (0.80, 0.85)  # persistence a day, share of the variance
VOLUME_SLOW = (0.94, 0.12)
VOLUME_MARKET_SD = 0.09
VOLUME_OWN_SD = 0.18
VOLUME_VOL_LINK = 3.5  # how far the day's volatility follows its activity (the persistent volume factors): the log of the volatility multiple per log of the volume factor
FLOW_SHARE = 0.7  # the share of a day's move (in standard deviations) that is order flow, spread over the day like its volume
VOLUME_FLOW_SIZE = 0.30  # a day's volume rises with the size of its order-flow imbalance (log per standard deviation)
VOLUME_FLOW_SELL = 0.02  # and more when sellers outweigh buyers (down days are busier)
FLOW_PROFILE_CAP = 2.5  # a minute counts at most this many times an average minute when order flow is spread over the day
VOLUME_COMMON_SD = 0.45  # the whole market's minute-to-minute swings in volume (index trading, news), shared by every symbol and by the market and sector variance
VOLUME_COMMON_RHO = 0.4
VOLUME_COMMON_LINK = 1.0  # the market's and sectors' minute variance follows it to this power
VOLUME_VARIANCE_LINK = 1.0  # a minute's own-noise variance follows its volume surprise to this power
VOLUME_DOW = (-0.037, -0.017, -0.007, 0.016, 0.042)  # Monday to Friday, log
VOLUME_SPIKE = 0.012  # chance a symbol has a heavy day of its own (news, a block trade, a rebalance)
VOLUME_SPIKE_SIZE = 0.7  # which adds this many times the day's usual volume on average
# News brings a burst of volume, NEWS_VOLUME_BURST times more, fading over NEWS_VOLUME_DECAY minutes.
NEWS_VOLUME_BURST, NEWS_VOLUME_DECAY = 10.0, 30.0  # per standard deviation of the jump: a 4-sigma jump trades about four times a normal day
NEWS_BURST_CAP = 5.0  # the t-tailed jump sizes count at most this many standard deviations in the burst (at most 50x a minute, about 4 normal days)
JUMP_VOLUME = 25.0  # a market-wide jump (selloff, shock onset) adds this times its log size times the symbol's beta to the day's volume multiple

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
MODEL_VERSION = 20  # 20: scheduled releases (jobs, CPI, GDP, PCE, FOMC) make a simulated session busier and wider, ordinary ones a little quieter; 19: each symbol's day-level volume persists as its own fit says (TSLA's for months, KO's for days); 18: funds have no company-news jumps (SPY once jumped 10% in a minute on "news"), news and selloffs trade in proportion to their size; 17: order flow first: a persistent activity state and a signed imbalance drive the day's volume, volatility and direction, and the market's and each symbol's minute volume drive the minute variance (volume no longer follows the price); 16: daily volume clumps over days the way the market's does (persistent market and own factors, the size of the day's move, weekdays; minute noise no longer changes the day's total); 15: volume as uneven as the market's (persistent, bursty minute volume; busy and quiet days; heavy days; expiration, month-end and Russell days; VOLUME_ELASTICITY 0.25); 14: early closes (1:00 PM) and holiday volume and volatility; 13: a crisis runs at its fitted volatility (STATE_LEVEL_ADJ 2.4, was 1.8); 12: volume follows each symbol's fitted profile for the regime state, with a day-to-day close-heavy/open-heavy mode (was one U shape); 11: volume follows the variance more gently (it fell to a third in calm markets); 10: circuit breakers (market-wide halts) and a smaller shock multiple in a crisis; 9: the long-lived regime model (calm / choppy / crisis) sets the market's drift, volatility level and selloff odds; 8: a backfill leaves replayed real days unscaled; 7: variances follow the real VIX on replayed days; 6: replays the real past; 5: dividends (ex-dividend drops); 4: exchange holidays (the trading days changed)
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
    shock: float = 0.0  # extra volatility from an uncertainty shock, as a multiple of normal (0: none)
    state: int = 0  # the long-lived regime (index into regime.STATES) at the end of the previous session
    z: np.ndarray | None = None  # the standardized macro features that drive its transitions
    phase: int = 0  # sessions into the regime model's period (0: the next session may change state)
    volume_state: np.ndarray | None = None  # (2, 1 + N) the fast and slow parts of the day-level volume (market, then each symbol); None: all zero


@dataclass
class _Draws:
    """A session's random draws for the skeleton. Paths are the market factor,
    then each sector, then each symbol's own noise."""

    gap: np.ndarray  # (P,) unit-variance overnight gap shocks
    within: np.ndarray  # (M, P) shocks from each minute's open to its close
    between: np.ndarray  # (M, P) shocks from each minute's close to the next one's open
    u: np.ndarray  # (M, 2) regime switching
    extremes: np.ndarray  # (M, N, 2) uniforms in (0, 1] for each minute's high and low
    volume_noise: np.ndarray  # (M, N) log-normal draws (the log over VOLUME_NOISE is a unit normal)
    news_minute: np.ndarray  # (N,) news arrives after this minute's close, or -1 for none
    news_size: np.ndarray  # (N,) unit-variance news jump sizes
    earnings: np.ndarray  # (N,) unit-variance earnings moves
    crash_u: float  # a selloff happens if this uniform is small enough
    crash_size: float  # unit exponential
    minute_seed: np.ndarray  # (M,) the seed each minute's seconds are drawn from
    shock_u: float = 1.0  # an uncertainty shock begins if this uniform is small enough
    shock_size: float = 0.0  # uniform: how big
    regime_u: float = 0.5  # picks the regime's next state
    regime_z: np.ndarray | None = None  # unit normals for the macro features' next values
    volume_score: np.ndarray | None = None  # (N,) unit normals: how close-heavy each symbol's volume is today
    volume_day: np.ndarray | None = None  # (N, 6): a symbol's day-level volume: white normal, heavy-day uniform and exponential, fast and slow AR normals, order-flow imbalance normal
    volume_burst: np.ndarray | None = None  # (M, N) uniforms: which minutes' volume bursts
    volume_common: np.ndarray | None = None  # (M,) unit normals: the market's minute-to-minute volume swings
    volume_base: tuple[np.ndarray, np.ndarray, np.ndarray] | None = None  # (a second new epoch in the same day leaves this at the first one's: the past still never depends on the future)  # the noise and bursts before a new epoch took over part of the day (what the day's normalisation uses, so the past never depends on the future)
    volume_market: np.ndarray | None = None  # (4,) unit normals for the market's day: fast, slow, white and order-flow imbalance

    def __post_init__(self) -> None:  # draws made by hand (tests) need not set the volume ones
        n = len(self.earnings)
        if self.volume_day is None:
            self.volume_day = np.zeros((n, 6))
        if self.volume_market is None:
            self.volume_market = np.zeros(4)
        if self.volume_common is None:
            self.volume_common = np.zeros(len(self.u))


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
    shock: float = 0.0  # the uncertainty shock in force this session (extra volatility)
    shock_onset: bool = False  # it began at this session's open
    state: int = 0  # the long-lived regime in force this session
    variance: float = 1.0  # the market variance multiple in force at the open (the state's level included)
    scale: float = 1.0  # the volatility multiple a shock adds (1: none)
    halted: np.ndarray | None = None  # (M,) minutes trading is halted (prices frozen, no volume)
    halts: tuple = ()  # (first halted minute, minute trading resumes (M: not today), level) for each halt
    noted_halts: tuple = ()  # on a replayed real day: the halts the real market had (found from the pinned path; nothing is frozen)
    vix: np.ndarray | None = None  # the volatility index at each half hour and the close (None: no SPY)
    minutes: int = SESSION_MINUTES  # minutes the session is open (210 on an early close; later minutes hold the close, halted, no volume)


@dataclass(frozen=True)
class OptionInputs:
    open: float  # the session's opening price
    prev_close: float
    ratio: np.ndarray  # (M+1,) volatility vs normal for second s at index 0 if s == 0, else (s-1)//60 + 1
    spread: np.ndarray  # (M,) the stock's spread factor by minute
    closes5: np.ndarray  # price at the end of each 5-minute stretch
    volume: np.ndarray  # (M+1,) shares traded before each minute
    minute_seed: int  # the session's last minute seed: a draw keyed to this market's path
    minute_seeds: np.ndarray  # (M,) every minute's seed, likewise
    opens: np.ndarray  # (M,) the price at each minute's first second


PERSISTENCE_FILE = Path(__file__).with_name("volume_persistence.json")


@dataclass(frozen=True)
class _Persistence:
    """Each symbol's day-level volume state: how big its busy and quiet spells are (the market's part and its own, in
    log volume) and how they persist (the shares of its own activity that are fast, slow or white; the slow rate).
    Fitted offline by `scripts/fit_volume_persistence.py` (TSLA's volume stays high or low for months, KO's does not);
    a symbol with no fit takes the global values."""

    market_sd: np.ndarray
    own_sd: np.ndarray
    fast_share: np.ndarray
    slow_share: np.ndarray
    slow_rho: np.ndarray
    flow_size: np.ndarray  # how much of its volume follows the day's order-flow imbalance (a white part: stickier symbols have less)

    @classmethod
    def default(cls, n: int) -> "_Persistence":
        return cls(np.full(n, VOLUME_MARKET_SD), np.full(n, VOLUME_OWN_SD), np.full(n, VOLUME_FAST[1]), np.full(n, VOLUME_SLOW[1]), np.full(n, VOLUME_SLOW[0]), np.full(n, VOLUME_FLOW_SIZE))

    @property
    def flow_mean(self) -> np.ndarray:
        return np.array([_flow_mean(a) for a in self.flow_size])

    @classmethod
    def load(cls, specs, path: Path | None = None) -> "_Persistence":
        path = path or PERSISTENCE_FILE  # (looked up now, so a fitting script can point it elsewhere)
        out = cls.default(len(specs))
        if not path.exists():
            return out
        fits = json.loads(path.read_text()).get("symbols", {})
        arrays = {k: getattr(out, k).copy() for k in ("market_sd", "own_sd", "fast_share", "slow_share", "slow_rho", "flow_size")}
        for i, spec in enumerate(specs):
            if fit := fits.get(spec.symbol):
                for k, a in arrays.items():
                    a[i] = fit.get(k, a[i])
        over = arrays["fast_share"] + arrays["slow_share"]  # rounded shares can add to a hair over 1: the white rest must stay real
        scale = np.where(over > 1.0, 1.0 / over, 1.0)
        arrays["fast_share"], arrays["slow_share"] = arrays["fast_share"] * scale, arrays["slow_share"] * scale
        return cls(**arrays)


class MarketEngine:
    def __init__(
        self,
        start: datetime,
        seed: int = 42,
        specs: tuple[SymbolSpec, ...] = SYMBOLS,
        prices: np.ndarray | None = None,
        real: RealBars | None = None,
    ) -> None:
        """A new market at `start`, its previous closes at `prices` (default: the specs').
        With `real` bars, every session up to the newest real day follows them (8e)."""
        self.specs = specs
        self._real = real
        vol = np.array([s.vol for s in specs])
        self._rho = np.array([s.market_corr for s in specs])
        self._beta = self._rho * vol / MARKET_VOL
        self._daily_volume = np.array([s.daily_volume for s in specs], dtype=float)
        self._persist = _Persistence.load(specs)
        self._earnings_move = np.array([s.earnings_move for s in specs])
        self._news_sd = NEWS_SIZE * vol / math.sqrt(TRADING_DAYS_PER_YEAR) * (self._earnings_move > 0)  # funds (SPY, QQQ...) have no company news; their moves come through the market
        self.sectors = sorted({s.sector for s in specs if s.sector})
        self._spy = next((i for i, sp in enumerate(specs) if sp.symbol == "SPY"), None)
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
        self._day_sd = np.sqrt(np.maximum(vol**2 - jump_var, 0.5 * vol**2) / TRADING_DAYS_PER_YEAR / SHOCK_VARIANCE)
        # Selloffs would pull the drift down; add their average back.
        self._drift = np.array([s.drift for s in specs]) + (CRASHES_PER_YEAR * crash_mean + SHOCK_DRIFT_LOSS) * self._beta
        # Dividends drop the price by a fraction of the previous close on each ex-date.
        # A spec's drift is its total return, so the continuous part makes up the
        # difference between the drops and the yield (about nothing).
        self._div_fraction = np.array([s.dividend_fraction for s in specs])
        payments = np.array([len(s.dividend_months) for s in specs])
        self._drift = self._drift - payments * (self._div_fraction + np.log1p(-self._div_fraction))
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

        # The long-lived regime (calm / choppy / crisis): its variance level, drift tilt
        # and selloff odds, centered so the long-run averages stay (up to the hand-tuned
        # STATE_LEVEL_ADJ, which lifts the average variance a few percent: whole-run volatility
        # 16.5% to 16.8% for the crisis retune, measured by scripts/check_engine_states.py).
        self._macro: RegimeParams = load_params()
        self._state_variance = self._macro.variance_multiple * np.array(STATE_LEVEL_ADJ)
        crash_by = np.array(CRASH_BY_STATE)
        self._state_crash = crash_by / float(self._macro.share @ crash_by)
        # The fitted drifts are what prices did (log returns, after volatility drag and
        # selloffs), and the engine takes both off again, so they are added back here. The
        # tilts average to zero over the long-run shares, so each symbol keeps its spec's drift.
        self._state_tilt = (
            self._macro.drift_tilt
            + 0.5 * MARKET_VOL**2 * (self._state_variance - float(self._macro.share @ self._state_variance))
            + CRASHES_PER_YEAR * (CRASH_MIN + CRASH_EXTRA) * (self._state_crash - 1.0)
        )

        self._p_switch = [1 - math.exp(-1 / (r[2] * 60)) for r in REGIMES]
        self._normal = next(i for i, r in enumerate(REGIMES) if r[0] == "normal")
        share = _stationary(self._p_switch)  # long-run share of time in each regime
        mults = np.array([r[1] for r in REGIMES])
        self._market_mults = mults / math.sqrt(share @ mults**2)
        self._own_mults = np.sqrt(mults) / math.sqrt(share @ mults)
        tilt = np.array([r[3] for r in REGIMES])
        self._tilt = (tilt - share @ tilt) / (TRADING_DAYS_PER_YEAR * (_S - 1))  # per second

        # Per-second standard deviation weights (in units of a normal day's): row 0
        # is the gap, rows 1.. the session, shaped through the day.
        self._gap_weight = math.sqrt(GAP_SHARE)
        self._second_weight = math.sqrt((1 - GAP_SHARE) / (_S - 1)) * _intraday_shape()  # rows 1..S-1
        self._usual_cum = np.cumsum(np.concatenate([[self._gap_weight**2], self._second_weight**2]))  # (S,) the variance accumulated by each second on a day with the usual regimes
        self._minute_weight = np.add.reduceat(self._second_weight**2, np.arange(0, _S - 1, 60))  # (M,) each minute's usual share of the day's variance
        self._volume = VolumeProfiles(specs, _M, STATES)

        self._seed = seed
        self._seeds_used = 0
        # Called with each session as the engine moves past it (its prices are final then).
        self.on_day_complete: Callable[[date], None] | None = None
        # Set by a finished backfill: what its unit-start prices were multiplied by.
        self.backfill_scale: np.ndarray | None = None
        # Set by `extend_real`: the old cutoff, after which stored bars no longer match.
        self.extended_from: date | None = None
        self._reset(start, seed, prices)

    # ----- time travel -------------------------------------------------------

    @property
    def time(self) -> datetime:
        return self._time

    @property
    def real_through(self) -> date | None:
        """The newest day this market replays the real bars of (None: none)."""
        return self._real_through if self._real is not None else None

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
        undone = [d for d, (_, at) in self._shocks.items() if at > t]
        if undone:  # a shock triggered after t never happened; the sessions it shaped are generated again
            for d in undone:
                del self._shocks[d]
            self._starts = {d: s for d, s in self._starts.items() if d <= market_day(t)}
            self.starts_version += 1
            self._forget(lambda d: d > market_day(t))
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

    def extend_real(self) -> bool:
        """Replay real bars that arrived after this market began: sessions after the old
        cutoff, simulated until now, are generated again from the real days (the ones up to
        today's are rebuilt now). Returns whether anything changed; `extended_from` is the
        old cutoff. The caller must make sure nothing was traded on the replaced days."""
        old, new = self._real_through, self._real.last_day if self._real else None
        if old is None or new is None or new <= old:
            return False
        today = market_day(self._time)
        self._real_through = new
        self.extended_from = old
        self._shocks = {d: s for d, s in self._shocks.items() if not old < d <= new}  # the real past can't be shocked
        self._vix_inputs.clear()
        self._quotes = None
        if old not in self._starts:  # the market was rewound to before the old cutoff: nothing after it exists yet
            return True
        self._starts = {d: s for d, s in self._starts.items() if d <= old}
        self._forget(lambda d: d > old)
        last = old
        while last < today:  # starts for the days already lived, from the real days
            self._starts[next_trading_day(last)] = self._skeleton(last).next_start
            last = next_trading_day(last)
        self.starts_version += 1
        return True

    def trigger_shock(self) -> date:
        """Start an uncertainty shock at the next session's open (sessions already
        generated beyond today are generated again). Returns that session."""
        today = market_day(self._time)
        day = next_trading_day(today)
        if self._real_through is not None and day <= self._real_through:
            raise ValueError("the real past can't be shocked; go past the newest real day first")
        if day in self._shocks:
            return day
        draw = np.random.default_rng([self._seed, day.toordinal(), _key("shock")]).random()
        # A trigger also sends the market into a crisis, whose own level is most of the stress.
        self._shocks[day] = ((SHOCK_EXCESS_MIN + SHOCK_EXCESS_RANGE * float(draw)) * SHOCK_IN_CRISIS, self._time)
        self._starts = {d: s for d, s in self._starts.items() if d <= today}
        self.starts_version += 1
        self._forget(lambda d: d > today)
        return day

    def reset(self, t: datetime, prices: np.ndarray | None = None) -> None:
        """Start a brand-new market at `t`, with fresh prices and no history, its
        previous closes at `prices` (default: the specs')."""
        self._reset(t, self._new_seed(), prices)

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
        """The market's long-lived regime today (calm, choppy or crisis)."""
        return STATES[self._skeleton(market_day(self._time)).state]

    @property
    def shock(self) -> float:
        """The extra volatility an uncertainty shock adds today (0: none)."""
        return self._skeleton(market_day(self._time)).shock

    @property
    def shock_visible(self) -> float:
        """The shock to show the user: its extra volatility while that exceeds `SHOCK_VISIBLE`, else 0."""
        shock = self.shock
        return shock if shock > SHOCK_VISIBLE else 0.0

    @property
    def shock_pending(self) -> bool:
        """A triggered shock waiting for the next open."""
        return next_trading_day(market_day(self._time)) in self._shocks

    def _regime_index(self) -> int:
        day = market_day(self._time)
        s = min(int(session_seconds(self._time)), _S - 1)
        return self._starts[day].regime if s == 0 else int(self._skeleton(day).regimes[(s - 1) // 60])

    def vol_ratio(self) -> np.ndarray:
        """(N,) each symbol's volatility now as a multiple of its normal level:
        the market factor's GARCH variance and regime for the part the market
        explains, the symbol's own variance and the regime for the rest (option
        prices start from this)."""
        day = market_day(self._time)
        start = self._starts[day]
        r = self._regime_index()
        own = start.own_variance if start.own_variance is not None else np.ones(len(self.specs))
        rho2 = self._rho**2
        sk = self._skeleton(day)
        var = rho2 * sk.variance * self._market_mults[r] ** 2 + (1 - rho2) * own * self._own_mults[r] ** 2
        return np.sqrt(var) * sk.scale

    def option_inputs(self, day: date, col: int) -> "OptionInputs":
        """What a session's option prices for one symbol depend on besides its
        price and the time (see options.price)."""
        sk = self._skeleton(day)
        cached = self._option_inputs.get((day, col))
        if cached and cached[0] is sk:  # same session generation (a new future makes another)
            return cached[1]
        inputs = self._option_inputs_of(sk, self._starts[day], col)
        if len(self._option_inputs) > 256:
            self._option_inputs.clear()
        self._option_inputs[(day, col)] = (sk, inputs)
        return inputs

    def _option_inputs_of(self, sk: _Skeleton, start: _DayStart, col: int, regime_weight: float = 1.0) -> "OptionInputs":
        """`regime_weight` below 1 lets the regime in force count for less (the index
        looks 30 days ahead; a regime lasts hours)."""
        own = 1.0 if start.own_variance is None else float(start.own_variance[col])
        rho2 = float(self._rho[col] ** 2)
        market_mults, own_mults = self._market_mults**regime_weight, self._own_mults**regime_weight
        by_regime = np.sqrt(rho2 * sk.variance * market_mults**2 + (1 - rho2) * own * own_mults**2) * sk.scale
        # Second s follows the regime of minute (s-1)//60; the open, the previous session's.
        regimes = np.concatenate([[start.regime], sk.regimes])
        return OptionInputs(
            open=float(np.exp(sk.open[0, col])),
            prev_close=float(start.prev_close[col]),
            ratio=by_regime[regimes],
            spread=sk.spread.copy(),
            closes5=np.exp(sk.close[4::5, col]),
            volume=sk.cum_volume[:, col].copy(),
            minute_seed=int(sk.minute_seed[-1]),
            minute_seeds=sk.minute_seed.copy(),
            opens=np.exp(sk.open[:, col]),
        )

    # ----- the volatility index ------------------------------------------------

    def _vix_samples(self, sk: _Skeleton, start: _DayStart) -> np.ndarray | None:
        """The index at the open, every half hour and the last second (14 values),
        from SPY's options (the model's own smile, unscaled: it needs no prices)."""
        from paper_trade.market import options  # options imports this module

        col = next((i for i, s in enumerate(self.specs) if s.symbol == "SPY"), None)
        if col is None:
            return None
        minutes = np.arange(0, sk.minutes, VIX_STEP_MINUTES)
        secs = np.append(minutes * 60, sk.minutes * 60 - 1)
        spots = np.append(np.exp(sk.open[minutes, col]), np.exp(sk.close[-1, col]))
        return options.vix(self.specs[col], self._option_inputs_of(sk, start, col, VIX_REGIME_WEIGHT), secs, spots)

    def vix_at(self, day: date, secs) -> np.ndarray:
        """The index at session seconds `secs` of a generated session."""
        from paper_trade.market import options

        col = self.symbol_index("SPY")
        secs = np.atleast_1d(np.asarray(secs))
        sk = self._skeleton(day)
        # A minute's first and last seconds are in the skeleton; others are generated.
        spots = np.array(
            [
                float(np.exp(sk.open[int(s) // 60, col])) if s % 60 == 0
                else float(np.exp(sk.close[int(s) // 60, col])) if s % 60 == 59
                else self.price_at(day, int(s), col)
                for s in secs
            ]
        )
        cached = self._vix_inputs.get(day)
        if cached is None or cached[0] is not sk:
            if len(self._vix_inputs) > 16:
                self._vix_inputs.clear()
            cached = self._vix_inputs[day] = (sk, self._option_inputs_of(sk, self._starts[day], col, VIX_REGIME_WEIGHT))
        return options.vix(self.specs[col], cached[1], secs, spots)

    def vix_bars(self, day: date, through: int | None = None) -> np.ndarray | None:
        """(bars, 5) half-hour bars of the index [open, high, low, close, volume 0]:
        each runs from one sample to the next. With `through` (a session second, for
        today): only bars begun by then, the last one closing at that second."""
        sk = self._skeleton(day)
        if sk.vix is None:
            return None
        values = sk.vix
        if through is not None:
            begun = min(int(through) // (VIX_STEP_MINUTES * 60) + 1, len(values) - 1)
            values = np.append(values[:begun], self.vix_at(day, min(int(through), _S - 1))[0])
        bars = np.zeros((len(values) - 1, 5))
        bars[:, 0], bars[:, 3] = values[:-1], values[1:]
        bars[:, 1], bars[:, 2] = np.maximum(values[:-1], values[1:]), np.minimum(values[:-1], values[1:])
        return bars

    def vix_quote(self) -> dict | None:
        """The index now as a quote (a dict like Quote.to_dict), None without SPY."""
        day = market_day(self._time)
        sk = self._skeleton(day)
        if sk.vix is None:
            return None
        sec = min(int(session_seconds(self._time)), _S - 1)
        last = float(self.vix_at(day, sec)[0])
        so_far = np.append(sk.vix[: sec // (VIX_STEP_MINUTES * 60) + 1], last)
        previous = previous_trading_day(day)
        prev = self._skeleton(previous).vix if previous in self._starts else None
        prev_close = float(prev[-1]) if prev is not None else float(sk.vix[0])
        last, prev_close = round(last, 2), round(prev_close, 2)
        change = round(last - prev_close, 2)
        return {
            "symbol": "VIX",
            "name": "Volatility Index",
            "last": last,
            "bid": last,
            "ask": last,
            "open": round(float(sk.vix[0]), 2),
            "high": round(float(so_far.max()), 2),
            "low": round(float(so_far.min()), 2),
            "prev_close": prev_close,
            "change": change,
            "change_pct": round(change / prev_close * 100, 2),
            "volume": 0,
            "bid_size": 0,
            "ask_size": 0,
        }

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
        # Real prices start where the real history says; the rest from 1.0, scaled at the end.
        prices = np.ones(len(self.specs))
        if self._real is not None and self._real_through is not None and day <= self._real_through:
            for i in range(len(self.specs)):
                close = self._real.close_before(i, day)
                if close is not None:
                    prices[i] = close
        start = self._start_at(prices, day)
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
            {
                d: _DayStart(s.prev_close * self.backfill_scale_on(d, scale), s.regime, s.variance, s.own_variance, s.shock, s.state, s.z, s.phase, s.volume_state)
                for d, s in unit.items()
            }
        )
        self.backfill_scale = scale
        if unit:
            self.origin = session_open(min(unit))
            self.starts_version += 1
        self.backfilled = True

    def backfill_scale_on(self, day: date, scale: np.ndarray | None = None) -> np.ndarray:
        """What a backfilled session's unit prices are multiplied by: `backfill_scale`,
        except for a symbol whose real bars the session replays (those are already
        real prices; the scale bridges to the origin's prices only after them)."""
        scale = self.backfill_scale if scale is None else scale
        if scale is None:
            return np.ones(len(self.specs))
        if self._real is None or self._real_through is None or day > self._real_through:
            return scale
        real = np.array([self._real.covers(i, day) for i in range(len(self.specs))])
        return np.where(real, 1.0, scale)

    def minute_bars(self, day: date, through: int | None = None) -> np.ndarray:
        """(minutes, N, 5) one-minute [open, high, low, close, volume] bars for a
        session, for the whole day or only through second `through`."""
        sk = self._skeleton(day)
        m_open = sk.minutes
        bars = np.empty((m_open, len(self.specs), 5))
        bars[..., 0] = np.exp(sk.open[:m_open])
        bars[..., 1] = np.exp(sk.high[:m_open])
        bars[..., 2] = np.exp(sk.low[:m_open])
        bars[..., 3] = np.exp(sk.close[:m_open])
        bars[..., 4] = sk.volume[:m_open]
        if through is None:
            return bars
        m, sec = divmod(min(through, m_open * 60 - 1), 60)
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
        if sk.halted is not None:
            candidates = candidates & ~sk.halted
        for m in first + np.flatnonzero(candidates[first : last + 1]):
            a, b = max(lo, 60 * m) - 60 * m, min(hi, 60 * m + 59) - 60 * m
            prices = self._fine(day, int(m), col)[a : b + 1, col]
            hits = np.flatnonzero(test(prices, float(sk.spread[m])))
            if hits.size:
                return int(60 * m + a + hits[0])
        return None

    def minute_prices(self, day: date, m: int, col: int) -> np.ndarray:
        """(60,) one symbol's price at each second of minute `m` of a session."""
        return self._fine(day, m, col)[:, col]

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
                    s.shock,
                    s.state,
                    None if s.z is None else s.z.tolist(),
                    s.phase,
                    None if s.volume_state is None else s.volume_state.tolist(),
                ]
                for d, s in self._starts.items()
            },
            "seed": self._seed,
            "seeds_used": self._seeds_used,
            "backfilled": self.backfilled,
            "model": MODEL_VERSION,
            "shocks": {d.isoformat(): [e, at.isoformat()] for d, (e, at) in self._shocks.items()},
            "symbols": [s.symbol for s in self.specs],
            "real_through": self._real_through.isoformat() if self._real_through else None,
        }

    @classmethod
    def from_state(cls, state: dict, specs: tuple[SymbolSpec, ...] = SYMBOLS, real: RealBars | None = None) -> "MarketEngine":
        engine = cls(datetime.fromisoformat(state["origin"]), seed=state["seed"], specs=specs, real=real)
        through = state.get("real_through")
        engine._real_through = date.fromisoformat(through) if through else None
        if engine._real_through and (real is None or real.last_day is None or real.last_day < engine._real_through):
            raise RealBarsGone("the saved market replays real bars that are no longer available")
        engine._seeds_used = state["seeds_used"]
        engine.backfilled = state.get("backfilled", False)
        engine._time = datetime.fromisoformat(state["time"])
        engine._epochs = [(datetime.fromisoformat(start), seed) for start, seed in state["epochs"]]
        engine._starts = {date.fromisoformat(d): _start_from(s) for d, s in state["starts"].items()}
        engine._shocks = {date.fromisoformat(d): (e, datetime.fromisoformat(at)) for d, (e, at) in state.get("shocks", {}).items()}
        return engine

    @classmethod
    def continue_older(cls, state: dict, specs: tuple[SymbolSpec, ...] = SYMBOLS, real: RealBars | None = None) -> "MarketEngine":
        """A market saved by another price model (or with other symbols) can't be
        regenerated, so start a new one at its time, from that session's previous
        closes (new symbols start at their spec prices). Its history is then
        generated again (`backfilled` is false)."""
        t = datetime.fromisoformat(state["time"])
        saved = state.get("symbols", LEGACY_SYMBOLS)
        prev = dict(zip(saved, state["starts"][market_day(t).isoformat()][0]))
        prices = np.array([prev.get(s.symbol, s.price) for s in specs])
        engine = cls(t, seed=state["seed"], specs=specs, prices=prices, real=real)
        engine._seeds_used = state["seeds_used"]
        return engine

    # ----- internals ---------------------------------------------------------

    def _reset(self, start: datetime, seed: int, prices: np.ndarray | None = None) -> None:
        self.origin = snap(start)
        self._time = self.origin
        # Sessions up to this day replay the real bars (a market keeps the data it began with).
        self._real_through: date | None = self._real.last_day if self._real else None
        self._epochs: list[tuple[datetime, int]] = [(self.origin, seed)]
        if prices is None:
            prices = np.array([s.price for s in self.specs])
        origin_day = market_day(self.origin)
        self._starts: dict[date, _DayStart] = {origin_day: self._start_at(prices, origin_day)}
        self._real_earnings: dict[int, list[date]] = {}
        self._shocks: dict[date, tuple[float, datetime]] = {}  # shocks the user triggered: onset session -> (excess, when)
        self._vix_inputs: dict[date, tuple[_Skeleton, OptionInputs]] = {}
        self._skeletons: OrderedDict[date, _Skeleton] = OrderedDict()
        self._option_inputs: dict[tuple[date, int], tuple[_Skeleton, OptionInputs]] = {}
        self._fines: OrderedDict[tuple[date, int], tuple[np.ndarray, np.ndarray]] = OrderedDict()
        self._quotes: tuple[datetime, dict[str, Quote]] | None = None
        self.backfilled = False
        # Bumped whenever session start states change, so savers can skip the large part.
        self.starts_version = getattr(self, "starts_version", 0) + 1

    def _start_at(self, prices: np.ndarray, day: date) -> _DayStart:
        """The state before `day`'s session: the real VIX's variance and the regime the real
        past's filter found, else the calm level and a calm regime."""
        before = previous_trading_day(day)
        found = self._real.regime_on(min(before, self._real_through)) if self._real and self._real_through else None
        state, z = found if found is not None else (0, None)
        variance = self._vix_variance(before) or float(self._state_variance[state])
        return _DayStart(prices, self._normal, variance, None, 0.0, state, z, 0)

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
        volume_burst = np.zeros((_M, n))
        volume_score = np.zeros(n)
        volume_day = np.zeros((n, 6))
        volume_market = np.zeros(4)
        volume_common = np.zeros(_M)
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
                shock_u, shock_size = rng.random(), rng.random()
                regime_u, regime_z = rng.random(), rng.standard_normal(len(self._macro.center))
                volume_market = rng.standard_normal(4)  # last in the stream, so the draws above are unchanged
                volume_common = rng.standard_normal(_M)
            elif j > k:
                i = j - 1 - k
                extremes[:, i] = 1 - rng.random((_M, 2))
                volume_noise[:, i] = rng.lognormal(0.0, VOLUME_NOISE, _M)
                if rng.random() < NEWS_PER_YEAR / TRADING_DAYS_PER_YEAR:
                    news_minute[i] = rng.integers(0, _M - 1)
                news_size[i] = rng.standard_t(JUMP_DF) * jump_scale
                earnings[i] = rng.standard_t(JUMP_DF) * jump_scale
                volume_score[i] = rng.standard_normal()
                volume_day[i] = rng.standard_normal(), rng.random(), rng.exponential(), rng.standard_normal(), rng.standard_normal(), rng.standard_normal()  # these are last in the stream, so every draw above is unchanged
                volume_burst[:, i] = rng.random(_M)
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
            shock_u,
            shock_size,
            regime_u,
            regime_z,
            volume_score,
            volume_day=volume_day,
            volume_burst=volume_burst,
            volume_market=volume_market,
            volume_common=volume_common,
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
            if draws.volume_base is None:
                draws.volume_base = (draws.volume_noise.copy(), draws.volume_burst.copy(), draws.volume_common.copy())
            new = self._random(seed, day)
            for name in ("within", "between", "u", "extremes", "volume_noise", "volume_burst", "volume_common", "minute_seed"):
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
        real_day = self._real_day(day)
        m_day = session_minutes(day)  # an early close uses the first part of the grid; the rest holds the close
        replayed = real_day.has if real_day is not None else np.zeros(n, dtype=bool)
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

        # An uncertainty shock scales every symbol's volatility; a new one begins at the open.
        forced = self._shocks.get(day)
        onset = 0.0
        if forced is not None:
            onset = forced[0]
        elif draws.shock_u < SHOCKS_PER_YEAR / TRADING_DAYS_PER_YEAR:
            onset = SHOCK_EXCESS_MIN + SHOCK_EXCESS_RANGE * draws.shock_size
        if real_day is not None:  # the real day carries any stress: the variance follows the real VIX
            onset = 0.0
        began = onset > start.shock
        excess = 0.0 if real_day is not None else max(start.shock, onset)
        # The long-lived regime may change at the start of each period; a triggered shock
        # sends the market into a crisis. It sets today's variance level, drift and selloff odds.
        macro = self._macro
        state = start.state
        z = start.z if start.z is not None else np.zeros(len(macro.center))
        h = start.variance
        if real_day is None:
            if start.phase == 0:
                p = macro.transition(z * macro.scale + macro.center)[state]
                state = min(int(np.searchsorted(np.cumsum(p), draws.regime_u)), len(p) - 1)
                z = np.clip(macro.level[state] + macro.persistence[state] * z + macro.noise[state] * draws.regime_z, -4.0, 4.0)
            if forced is not None:  # the user's trigger begins this session
                state = len(STATES) - 1
            after_real = self._real_through is not None and previous_trading_day(day) <= self._real_through
            if not after_real:  # the real VIX sets the first simulated day's variance as it is
                h = float(np.clip(start.variance * self._state_variance[state] / self._state_variance[start.state], *VARIANCE_RANGE))
        state_drift = 0.0 if real_day is not None else self._state_tilt[state]
        scale = 1 + excess

        # Each symbol's loadings on them, today.
        h_own = np.ones(n) if start.own_variance is None else start.own_variance
        holiday_volume, holiday_sd = (1.0, 1.0) if real_day is not None else day_effects(day)  # real days carry their own
        if real_day is None:  # scheduled releases (jobs, CPI, FOMC...) make a simulated day busier and wider; every session is divided by the average
            release_volume, release_sd = release_effects(day, volume_events(day))  # (which also holds the expiration / month-end volume)
            holiday_volume, holiday_sd = holiday_volume * release_volume, holiday_sd * release_sd
        market_load = self._rho * self._day_sd * math.sqrt(h) * scale * holiday_sd
        own_sd = np.sqrt(1 - self._rho**2) * self._day_sd * np.sqrt(h_own) * scale * holiday_sd
        sector_load = own_sd * self._sector_corr
        own_load = own_sd * np.sqrt(1 - self._sector_corr**2)
        earnings_on = self._earnings_on(day) & ~replayed
        news = np.flatnonzero((draws.news_minute >= 0) & ~replayed & (self._news_sd > 0))  # real days carry their own news
        # Activity and order flow come first: they set how much trades, how far the price moves and which way.
        # The day's activity (persistent, shared with volume) scales the day's volatility; a signed imbalance
        # (buyers against sellers) moves the price through the day in proportion to the volume that trades,
        # and a bigger or more one-sided imbalance trades more. Volume does not follow the price.
        volume_state = None  # real days leave the day-level state at zero
        garch_own_sd = own_sd
        activity = self._volume_activity(draws, start.volume_state, self._persist)
        volume_state = None if real_day is not None else activity[0]
        market_load = market_load * activity[2]
        own_load = own_load * np.where(replayed, 1.0, activity[3])  # a symbol with a real bar is pinned to it; the sector's share stays what it was
        shape = self._volume.shape(state, draws.volume_score)
        if m_day < _M:  # an early close trades EARLY_CLOSE_VOLUME of a normal day, in the shape of the open minutes
            shape = shape.copy()
            shape[:m_day] *= EARLY_CLOSE_VOLUME * _M / shape[:m_day].sum(axis=0)
        common = self._volume_common(draws.volume_common)  # (M,) the whole market's swings
        raw_noise = self._volume_noise(draws.volume_noise, draws.volume_burst) * common[:, None]
        noise = raw_noise
        reference = noise if draws.volume_base is None else self._volume_noise(draws.volume_base[0], draws.volume_base[1]) * self._volume_common(draws.volume_base[2])[:, None]
        # The minute noise decides how the day's volume is spread over its open minutes, not how much there is: a
        # busy minute leaves less for the others (the real day's total has its own, persistent, variation).
        # (Normalised on the reference noise: a new future starting mid-day must not change the minutes before it.)
        noise = noise * (shape[:m_day].sum(axis=0) / (shape[:m_day] * reference[:m_day]).sum(axis=0))
        level = self._daily_volume * VOLUME_LEVEL * (h * scale**2) ** VOLUME_ELASTICITY * np.where(earnings_on, EARNINGS_VOLUME, 1.0)
        level = level * holiday_volume
        market_flow, own_flow = draws.volume_market[3], np.where(replayed, 0.0, draws.volume_day[:, 5])
        spread = np.sqrt(market_load**2 + own_load**2)
        flow = np.divide(market_load * market_flow + own_load * own_flow, spread, out=np.zeros(n), where=spread > 0)  # unit variance
        # A selloff or a shock's onset is a burst of selling: it trades in proportion to its size.
        crash = 0.0
        if draws.crash_u < CRASHES_PER_YEAR * self._state_crash[state] / TRADING_DAYS_PER_YEAR and real_day is None:
            crash = CRASH_MIN + CRASH_EXTRA * draws.crash_size
        if began:
            crash += SHOCK_DROP * onset
        level = level * np.where(replayed, 1.0, 1.0 + JUMP_VOLUME * crash * np.abs(self._beta))  # a selloff trades more in every symbol, hedges included
        level = level * np.where(replayed, 1.0, self._volume_factor(activity[1], flow, draws, day, self._persist) * (volume_events(day) if real_day is not None else 1.0))
        news_burst = np.ones((_M, n))
        for i in news:
            after = np.arange(_M) - (draws.news_minute[i] + 1)
            news_burst[:, i] *= 1 + NEWS_VOLUME_BURST * min(max(abs(draws.news_size[i]), 0.5), NEWS_BURST_CAP) * np.where(after >= 0, np.exp(-after / NEWS_VOLUME_DECAY), 0)
        volume_raw = level / _M * shape * news_burst * noise
        volume_raw[m_day:] = 0
        # Variance moves with the minute's volume: a burst of trading is a burst of price movement (the day's
        # variance stays as it was; the symbol's own noise is what follows the volume).
        dv_min = self._minute_weight  # (the usual profile, not the day's regimes: those can change when a new future starts mid-day)
        minute_var = np.where(replayed, 1.0, raw_noise**VOLUME_VARIANCE_LINK)
        ref_var = np.where(replayed, 1.0, reference**VOLUME_VARIANCE_LINK)
        minute_var = minute_var * (dv_min[:m_day].sum() / (dv_min[:m_day, None] * ref_var[:m_day]).sum(axis=0))
        # The market's and the sectors' variance follows the market's volume swings (shared, so the factor structure stays).
        common_var = common**VOLUME_COMMON_LINK
        common_ref = (common if draws.volume_base is None else self._volume_common(draws.volume_base[2])) ** VOLUME_COMMON_LINK
        common_var = common_var * (dv_min[:m_day].sum() / (dv_min[:m_day] * common_ref[:m_day]).sum())

        # The unit paths (market, sectors, own) at the knots.
        steps = np.empty((2 * _M, 1 + k + n))
        own_cols = slice(1, None)
        for v, cols in ((v_market, slice(0, 1)), (v_own, own_cols)):
            dv = np.diff(v[knots])
            steps[0, cols] = math.sqrt(v[0]) * draws.gap[cols]
            steps[1::2, cols] = np.sqrt(dv[0::2])[:, None] * draws.within[:, cols]
            steps[2::2, cols] = np.sqrt(dv[1::2])[:, None] * draws.between[:-1, cols]
        steps[1::2, 1 + k :] *= np.sqrt(minute_var)
        steps[1::2, : 1 + k] *= np.sqrt(common_var)[:, None]
        paths = np.cumsum(steps, axis=0)
        unit_market, unit_own = paths[:, :1], paths[:, 1 + k :]
        # Part of the move is the day's order flow, spread over the day like its volume.
        expected = np.minimum(shape, FLOW_PROFILE_CAP)  # the closing cross and the opening rush trade a lot but don't move the price in proportion
        expected[m_day:] = 0
        cum = np.concatenate([np.zeros((1, n)), np.cumsum(expected, axis=0)])  # the day's expected volume profile
        share = cum / np.maximum(cum[-1], 1e-9)
        at_knots = np.empty((2 * _M, n))
        at_knots[0::2], at_knots[1::2] = share[:-1], share[1:]
        mkt_profile = np.minimum(self._volume.market_profile[state] * _M, FLOW_PROFILE_CAP)  # the market's own, whichever symbols are listed
        mkt_profile[m_day:] = 0
        mkt_cum = np.concatenate([[0.0], np.cumsum(mkt_profile)])
        mkt_share = np.empty((2 * _M, 1))
        mkt_share[0::2, 0], mkt_share[1::2, 0] = mkt_cum[:-1] / mkt_cum[-1], mkt_cum[1:] / mkt_cum[-1]
        c = FLOW_SHARE
        c_own = np.where(replayed, 0.0, c)
        end = m_day * 60 - 1  # the flow's size is the path's own at the close (less than a full day's on an early close; the usual, not the day's regimes, so a new future never changes earlier minutes)
        unit_market = math.sqrt(1 - c**2) * unit_market + c * market_flow * mkt_share * math.sqrt(self._usual_cum[end])
        unit_own = np.sqrt(1 - c_own**2) * unit_own + c_own * own_flow * at_knots * math.sqrt(self._usual_cum[end])


        # Log prices at the knots: the loaded paths, the drift (lognormal-corrected),
        # and the jumps.
        t = knots[:, None]
        log = (
            np.log(start.prev_close)
            + market_load * unit_market
            + sector_load * paths[:, 1 + self._sector_of]
            + own_load * unit_own
            + (self._drift + (SHOCK_REBOUND * excess + state_drift) * self._beta) / (TRADING_DAYS_PER_YEAR * (_S - 1)) * t
            + self._beta * tilt[knots][:, None] * holiday_sd  # the volatility regimes' drift scales with the day's volatility
            - 0.5 * (market_load**2 * v_market[knots][:, None] + (sector_load**2 + own_load**2) * v_own[knots][:, None])
        )
        em = np.where(earnings_on, self._earnings_move, 0.0)
        own_jump = draws.earnings * em - 0.5 * em**2
        log += own_jump - crash * self._beta
        log += np.where(self._dividend_on(day) & ~replayed, np.log1p(-self._div_fraction), 0.0)
        for i in news:
            size = draws.news_size[i] * self._news_sd[i] - 0.5 * self._news_sd[i] ** 2
            log[2 * (draws.news_minute[i] + 1) :, i] += size
            own_jump[i] += size

        # Each minute's high and low, from the bridge between its open and close.
        open_, close = log[0::2], log[1::2]
        dv_market = v_market[knots[1::2]] - v_market[knots[0::2]]
        dv_own = v_own[knots[1::2]] - v_own[knots[0::2]]
        var = (market_load**2 * dv_market[:, None] + sector_load**2 * dv_own[:, None]) * common_var[:, None] + own_load**2 * minute_var * dv_own[:, None]
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
        if real_day is not None:  # the real open, high, low and close (through the early close, if any)
            pinned = _pin(open_[:m_day], high[:m_day], low[:m_day], close[:m_day], real_day)
            open_, high, low, close = (np.concatenate([part, old[m_day:]]) for part, old in zip(pinned, (open_, high, low, close), strict=True))
        if m_day < _M:  # after an early close every price stays at the close
            open_, high, low, close = open_.copy(), high.copy(), low.copy(), close.copy()
            for part in (open_, high, low, close):
                part[m_day:] = close[m_day - 1]
        move = np.abs(close - open_)
        halted = np.zeros(_M, dtype=bool)
        halted[m_day:] = True
        halts: list[tuple[int, int, int]] = []
        noted: list[tuple[int, int, int]] = []
        if self._spy is not None:
            found = self._halts(float(np.log(start.prev_close[self._spy])), low[:, self._spy], min(BREAKER_LAST_MINUTE, m_day - (_M - BREAKER_LAST_MINUTE)) if real_day is None else m_day, m_day)
            if real_day is None:
                halts = found
            elif day in REAL_HALTS and real_day.has[self._spy]:
                noted = [h for h in found if h[2] <= REAL_HALTS[day]]  # real prices aren't frozen; the breaker is only marked (the time of day is the generated path's, so the 3:25 PM cutoff doesn't apply)
        if halts:  # a market-wide halt: every price freezes where it stood, then trading resumes
            open_, high, low, close = open_.copy(), high.copy(), low.copy(), close.copy()
            for first, resume, _ in halts:
                halted[first:resume] = True
                frozen = close[first - 1].copy()
                for part in (open_, high, low, close):
                    part[first:resume] = frozen
            move = np.abs(close - open_)

        # Volume was made before the price; here it only meets what the day did to it (a real day's total, halts).
        volume = np.floor(volume_raw)
        volume[m_day:] = 0
        if real_day is not None:  # the real day's volume, spread over the minutes it was open
            volume = np.concatenate([_pin_volume(volume[:m_day], real_day), volume[m_day:]])
        volume[halted] = 0
        cum_volume = np.zeros((_M + 1, n))
        cum_volume[1:] = np.cumsum(volume, axis=0)

        # Spreads: by the market's volatility this minute relative to normal.
        normal_second = math.sqrt((1 - GAP_SHARE) / (_S - 1))
        spread_factor = np.clip(np.sqrt(dv_market * h / 59) * scale / normal_second, 1.0, MAX_SPREAD_FACTOR)

        # The day's shocks (a jump counts in full) set the next session's variances.
        market_day_sd = MARKET_VOL / math.sqrt(TRADING_DAYS_PER_YEAR) * math.sqrt(h)
        last_knot = 2 * m_day - 1  # the close (an early close ends the day's variance there)
        shock = unit_market[last_knot, 0] - crash / market_day_sd
        response = GARCH_ALPHA + (GARCH_GAMMA if shock < 0 else 0.0)
        h_next = float(np.clip(self._omega * self._state_variance[state] + GARCH_BETA * h + response * shock**2 * h, *VARIANCE_RANGE))
        own_shock = self._sector_corr * paths[last_knot, 1 + self._sector_of] + np.sqrt(1 - self._sector_corr**2) * unit_own[last_knot]
        own_shock += np.divide(own_jump, garch_own_sd, out=np.zeros(n), where=garch_own_sd > 0)
        own_response = OWN_GARCH_ALPHA + np.where(own_shock < 0, OWN_GARCH_GAMMA, 0.0)
        h_own_next = np.where(
            own_sd > 0,
            np.clip(self._own_omega + OWN_GARCH_BETA * h_own + own_response * own_shock**2 * h_own, *VARIANCE_RANGE),
            1.0,
        )

        if real_day is not None:
            # Real days set the next variances from the real VIX and the real returns.
            vix_h = self._vix_variance(day)
            if vix_h is not None:
                h_next = vix_h
            h_own_next = self._filter_own(real_day, start, close[-1], own_sd, h_own, h_own_next)
        next_state, next_z, next_phase = state, z, (start.phase + 1) % macro.period
        if real_day is not None:  # the real past: the state the model's filter finds, from the real returns
            found = self._real.regime_on(day) if self._real else None
            if found is not None:
                next_state, next_z = found
            next_phase = 0
        shock_next = excess * _SHOCK_DECAY
        shock_next = shock_next if shock_next > 0.01 else 0.0
        sk = _Skeleton(
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
            next_start=_DayStart(np.exp(close[-1]), int(regimes[m_day - 1]), h_next, h_own_next, shock_next, next_state, next_z, next_phase, volume_state),
            shock=excess,
            shock_onset=began,
            state=state,
            variance=h,
            scale=scale,
            halted=halted,
            halts=tuple(halts),
            noted_halts=tuple(noted),
            minutes=m_day,
        )
        sk.vix = self._vix_samples(sk, start)
        return sk

    @staticmethod
    def _volume_common(z: np.ndarray) -> np.ndarray:
        """(M,) the whole market's minute-to-minute swings in volume (mean 1): an AR(1) log-normal."""
        y = np.empty_like(z)
        y[0] = z[0]
        step = math.sqrt(1 - VOLUME_COMMON_RHO**2)
        for m in range(1, len(z)):
            y[m] = VOLUME_COMMON_RHO * y[m - 1] + step * z[m]
        return np.exp(VOLUME_COMMON_SD * y - VOLUME_COMMON_SD**2 / 2)

    def _volume_noise(self, lognormal: np.ndarray, burst_u: np.ndarray | None) -> np.ndarray:
        """(M, N) multiples of the profile (mean 1): a log-normal that persists from minute to minute
        (each symbol's fitted persistence), with occasional bursts. Real minute volume is clumpy; an
        independent log-normal made 5-minute volume far smoother than the market's. `lognormal` are the
        per-minute log-normal draws (their log over VOLUME_NOISE is a unit normal), `burst_u` uniforms."""
        rho = np.clip(self._volume.minute_ar1, 0.0, 0.95)
        z = np.log(lognormal) / VOLUME_NOISE
        y = np.empty_like(z)
        y[0] = z[0]
        step = np.sqrt(1 - rho**2)
        for m in range(1, len(z)):
            y[m] = rho * y[m - 1] + step * z[m]
        noise = np.exp(VOLUME_NOISE * y - VOLUME_NOISE**2 / 2)
        if burst_u is None:
            return noise
        burst = np.where(burst_u < VOLUME_BURST, 1 - VOLUME_BURST_SIZE * np.log(np.maximum(burst_u, 1e-12) / VOLUME_BURST), 1.0)
        return noise * burst / (1 + VOLUME_BURST * VOLUME_BURST_SIZE)

    @staticmethod
    def _volume_activity(draws: _Draws, state: np.ndarray | None, persist: "_Persistence | None" = None) -> tuple[np.ndarray, np.ndarray, float, np.ndarray]:
        """The day's activity: the market's and each symbol's busy or quiet day, persisting over days (a fast
        and a slow AR(1) plus a white part). Returns the state to carry to tomorrow, the unit-variance activity of
        the market (first) and of each symbol, and the multiples of volatility they bring (mean square 1)."""
        n = len(draws.volume_day)
        old = state if state is not None and state.shape == (2, 1 + n) else np.zeros((2, 1 + n))  # a state saved for other symbols starts again
        persist = persist or _Persistence.default(n)
        fast_rho = np.full(1 + n, VOLUME_FAST[0])  # the market and every symbol share the fast part; the slow rate and the shares are each symbol's own
        slow_rho = np.concatenate([[VOLUME_SLOW[0]], persist.slow_rho])
        fast_share = np.concatenate([[VOLUME_FAST[1]], persist.fast_share])
        slow_share = np.concatenate([[VOLUME_SLOW[1]], persist.slow_share])
        white = np.maximum(1 - fast_share - slow_share, 0.0)
        eps_fast = np.concatenate([[draws.volume_market[0]], draws.volume_day[:, 3]])
        eps_slow = np.concatenate([[draws.volume_market[1]], draws.volume_day[:, 4]])
        eps_white = np.concatenate([[draws.volume_market[2]], draws.volume_day[:, 0]])
        new = np.stack([fast_rho * old[0] + np.sqrt(1 - fast_rho**2) * eps_fast, slow_rho * old[1] + np.sqrt(1 - slow_rho**2) * eps_slow])
        comps = np.sqrt(fast_share) * new[0] + np.sqrt(slow_share) * new[1] + np.sqrt(white) * eps_white
        link = VOLUME_VOL_LINK  # (the volatility link keeps the global sizes, not the fitted ones: a symbol's volume persistence changes, its price statistics do not)
        market_mult = math.exp(link * VOLUME_MARKET_SD * comps[0] - (link * VOLUME_MARKET_SD) ** 2)
        own_mult = np.exp(link * VOLUME_OWN_SD * comps[1:] - (link * VOLUME_OWN_SD) ** 2)
        return new, comps, market_mult, own_mult

    @staticmethod
    def _volume_factor(comps: np.ndarray, flow: np.ndarray, draws: _Draws, day: date, persist: "_Persistence | None" = None) -> np.ndarray:
        """(N,) the day's volume against the usual (mean 1): its activity, the size and side of its order-flow
        imbalance `flow` (unit variance; negative: sellers), the day of the week, and now and then a heavy day."""
        persist = persist or _Persistence.default(len(comps) - 1)
        log_activity = persist.market_sd * comps[0] + persist.own_sd * comps[1:] - (persist.market_sd**2 + persist.own_sd**2) / 2
        imbalance = np.exp(persist.flow_size * np.abs(flow) - VOLUME_FLOW_SELL * flow) / persist.flow_mean
        spike = np.where(draws.volume_day[:, 1] < VOLUME_SPIKE, 1 + VOLUME_SPIKE_SIZE * draws.volume_day[:, 2], 1.0) / (1 + VOLUME_SPIKE * VOLUME_SPIKE_SIZE)
        weekday = math.exp(VOLUME_DOW[day.weekday()] - _DOW_MEAN) if day.weekday() < 5 else 1.0
        return np.exp(log_activity) * imbalance * spike * weekday

    @staticmethod
    def _halts(prev_log: float, spy_low: np.ndarray, last_minute: int = BREAKER_LAST_MINUTE, end: int = _M) -> list[tuple[int, int, int]]:
        """The circuit-breaker halts of a session from SPY's minute lows (log prices) and its
        previous close: (first halted minute, minute trading resumes, level). A level is
        crossed once; one crossed while trading is open halts from the next minute."""
        out: list[tuple[int, int, int]] = []
        level, m = 0, 0
        while level < len(BREAKER_LEVELS) and m < end:
            hit = np.flatnonzero(spy_low[m:] <= prev_log + math.log(1 - BREAKER_LEVELS[level]))
            if hit.size == 0:
                break
            k = m + int(hit[0])
            last = level == len(BREAKER_LEVELS) - 1
            if not last and k >= last_minute:  # too late in the session for a pause: only the last level counts
                level = len(BREAKER_LEVELS) - 1
                continue
            first = k + 1
            if first >= end:
                break
            resume = end if last else min(first + HALT_MINUTES, end)
            out.append((first, resume, level + 1))
            m, level = resume, level + 1
        return out

    @property
    def halt(self) -> dict | None:
        """The circuit-breaker halt in force now: its level (1-3) and when trading resumes
        (None: not today), or None when trading is open."""
        day = market_day(self._time)
        sk = self._skeleton(day)
        minute = min(int(session_seconds(self._time)) // 60, sk.minutes - 1)
        for first, resume, level in sk.halts:
            if first <= minute < resume:
                when = None if resume >= sk.minutes else (session_open(day) + timedelta(minutes=resume)).isoformat()
                return {"level": level, "resumes": when}
        return None

    def halts_of(self, day: date) -> list[dict]:
        """The circuit-breaker halts of a session: when each began (Unix seconds), when trading
        resumed (None: not that day), its level, and whether it is an estimate (a listed real halt,
        `REAL_HALTS`, placed on the generated path inside the real bar; other real days have none)."""
        out = []
        if day not in self._starts:  # a day the market hasn't reached (or no longer keeps)
            return out
        sk = self._skeleton(day)
        opened, end = session_open(day), sk.minutes
        for real, found in ((False, sk.halts), (True, sk.noted_halts)):
            for first, resume, level in found:
                if real and resume >= end:  # a real day never closed early: the estimate is a pause like the others
                    resume = min(first + HALT_MINUTES, end)
                out.append(
                    {
                        "start": int((opened + timedelta(minutes=first)).timestamp()),
                        "resume": None if resume >= end and not real else int((opened + timedelta(minutes=min(resume, end))).timestamp()),
                        "level": level,
                        "estimated": real,
                    }
                )
        return out

    def halted_at(self, day: date, second: int) -> bool:
        sk = self._skeleton(day)
        return bool(sk.halted is not None and sk.halted[min(second, _S - 1) // 60])

    def next_trading_second(self, day: date, second: int) -> int | None:
        """The first second at or after `second` when trading is open (None: not today)."""
        sk = self._skeleton(day)
        if sk.halted is None:
            return second
        for m in range(min(second, _S - 1) // 60, _M):
            if not sk.halted[m]:
                return max(second, 60 * m)
        return None

    def reopens_at(self, day: date, second: int) -> bool:
        """Whether `second` is the first of a session or the first after a halt (a gap there fills at the new price)."""
        return second == 0 or any(resume * 60 == second for _, resume, _ in self._skeleton(day).halts)

    def _filter_own(self, real_day: RealDay, start: _DayStart, last: np.ndarray, own_sd: np.ndarray, h_own: np.ndarray, generated: np.ndarray) -> np.ndarray:
        """Each symbol's own variance for the next session, from its real return today less
        the market's (SPY's real return times the symbol's beta), as the model does with
        its own shocks. Symbols without a bar keep the generated value."""
        spy = self._spy
        if spy is None or not real_day.has[spy]:
            return generated
        total = last - np.log(start.prev_close) + np.log1p(np.where(real_day.has, real_day.dividend, 0.0) / np.exp(last))
        resid = total - self._beta * total[spy]
        usable = real_day.has & (own_sd > 0)
        own_shock = np.divide(resid, own_sd, out=np.zeros_like(resid), where=usable)
        response = OWN_GARCH_ALPHA + np.where(own_shock < 0, OWN_GARCH_GAMMA, 0.0)
        filtered = np.clip(self._own_omega + OWN_GARCH_BETA * h_own + response * own_shock**2 * h_own, *VARIANCE_RANGE)
        return np.where(usable, filtered, generated)

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

    def _vix_variance(self, day: date) -> float | None:
        """The market variance multiple that makes the sim's VIX the real one of `day`'s
        close (None where the real VIX isn't known or the day isn't replayed)."""
        if self._real is None or self._real_through is None or day > self._real_through:
            return None
        vix = self._real.vix_on_or_before(day)
        if vix is None:
            return None
        return float(np.clip(((vix / VIX_BIAS) ** 2 - VIX_BASE2) / VIX_SLOPE, *VARIANCE_RANGE))

    def earnings_dates(self, col: int, start: date, end: date) -> list[date]:
        """Sessions in [start, end] that open with `col`'s earnings reaction: the real
        ones (found from volume spikes near the usual dates) on replayed days, the
        model's after them."""
        spec = self.specs[col]
        model = earnings_days(spec, start, end)
        real, through = self._real, self._real_through
        first = real.first_day(col) if real else None
        if real is None or through is None or first is None or spec.earnings is None:
            return model
        if col not in self._real_earnings:
            self._real_earnings[col] = [real.spike_near(col, d, market=self._spy) or d for d in earnings_days(spec, first, through)]
        found = [d for d in self._real_earnings[col] if start <= d <= end]
        tail = [d for d in model if d > through or d < first]
        return sorted(set(found + tail))

    def _real_day(self, day: date) -> RealDay | None:
        """The real bars `day` replays (None after the newest real day or with none)."""
        if self._real is None or self._real_through is None or day > self._real_through:
            return None
        return self._real.day(day)

    def _dividend_on(self, day: date) -> np.ndarray:
        """(N,) whether each symbol goes ex-dividend at this session's open."""
        return np.array([day in dividend_days(s, day, day) for s in self.specs], dtype=bool)

    def dividend(self, day: date, col: int) -> float:
        """The dividend per share going ex at `day`'s open, 0 if none (the real one on
        a replayed day)."""
        if self._replays(col, day):
            return round(sum(a for _, a in self._real.dividends(col, day, day)), 4)  # type: ignore[union-attr]
        if day not in dividend_days(self.specs[col], day, day):
            return 0.0
        self._skeleton(day)  # the day's start exists once the session is generated
        return round(float(self._div_fraction[col] * self._starts[day].prev_close[col]), 2)

    def dividend_schedule(self, col: int, start: date, end: date) -> list[tuple[date, float]]:
        """Ex-dividend dates in [start, end] with each payment as a fraction of the price before it:
        the real ones on replayed days, the symbol's model after them."""
        spec = self.specs[col]
        through = self._real_through
        if self._real is None or through is None:
            return [(d, spec.dividend_fraction) for d in dividend_days(spec, start, end)]
        first = self._real.first_day(col)
        out = []
        if first is not None and start <= through and end >= first:
            real_end = min(end, through)
            out += [(d, a / (self._real.close_before(col, d) or a * 100)) for d, a in self._real.dividends(col, max(start, first), real_end)]
            if start < first:
                out += [(d, spec.dividend_fraction) for d in dividend_days(spec, start, first - timedelta(days=1))]
        elif start <= through:
            out += [(d, spec.dividend_fraction) for d in dividend_days(spec, start, min(end, through))]
        if end > through:
            out += [(d, spec.dividend_fraction) for d in dividend_days(spec, max(start, through + timedelta(days=1)), end)]
        return sorted(out)

    def _replays(self, col: int, day: date) -> bool:
        return (
            self._real is not None
            and self._real_through is not None
            and day <= self._real_through
            and self._real.covers(col, day)
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
            on[i] = day == _earnings_session(day.year, day.month, dom)
        return on


def _pin(open_: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray, real: RealDay) -> tuple[np.ndarray, ...]:
    """Bend each symbol's generated minute path (log prices; (M, N) arrays) so the day
    opens, peaks, bottoms and closes at the real bar. A linear correction through
    anchors (the open, the generated high's and low's knots, the close) keeps the
    generated noise in between; anything left outside the real range is folded in, and
    the minutes holding the extremes are set to them exactly. Symbols without a real
    bar keep their generated path."""
    m, n = open_.shape
    cols = np.flatnonzero(real.has)
    if len(cols) == 0:
        return open_, high, low, close
    o, c = np.log(real.open[cols]), np.log(real.close[cols])
    hi = np.maximum(np.log(real.high[cols]), np.maximum(o, c))
    lo = np.minimum(np.log(real.low[cols]), np.minimum(o, c))
    k = 2 * m
    y = np.empty((k, len(cols)))
    y[0::2], y[1::2] = open_[:, cols], close[:, cols]
    gh, gl = high[:, cols], low[:, cols]
    mh, ml = np.argmax(gh, axis=0), np.argmin(gl, axis=0)
    # The anchors sit on the knots (minute open or close) nearest each generated extreme.
    ah = np.clip(2 * mh + (y[2 * mh + 1, np.arange(len(cols))] > y[2 * mh, np.arange(len(cols))]), 1, k - 2)
    al = np.clip(2 * ml + (y[2 * ml + 1, np.arange(len(cols))] < y[2 * ml, np.arange(len(cols))]), 1, k - 2)
    al = np.where(al == ah, np.where(ah > 1, ah - 1, ah + 1), al)
    rows = np.arange(len(cols))
    th = hi - np.maximum(gh[mh, rows] - y[ah, rows], 0.0)  # the knot's target: the real high less the minute's own excursion
    tl = lo + np.maximum(y[al, rows] - gl[ml, rows], 0.0)
    xs = np.stack([np.zeros_like(ah), np.minimum(ah, al), np.maximum(ah, al), np.full_like(ah, k - 1)])  # (4, C)
    first_is_high = ah < al
    offs = np.stack(
        [
            o - y[0, rows],
            np.where(first_is_high, th - y[ah, rows], tl - y[al, rows]),
            np.where(first_is_high, tl - y[al, rows], th - y[ah, rows]),
            c - y[k - 1, rows],
        ]
    )
    t = np.arange(k)[:, None]
    correction = offs[0] + sum(
        (offs[s + 1] - offs[s]) * np.clip((t - xs[s]) / np.maximum(xs[s + 1] - xs[s], 1), 0.0, 1.0) for s in range(3)
    )
    y = _fold(y + correction, lo, hi)
    new_open, new_close = y[0::2], y[1::2]
    mid = (correction[0::2] + correction[1::2]) / 2
    new_high = np.clip(np.maximum(gh + mid, np.maximum(new_open, new_close)), lo, hi)
    new_low = np.clip(np.minimum(gl + mid, np.minimum(new_open, new_close)), lo, hi)
    new_high[mh, rows], new_low[ml, rows] = hi, lo
    new_high[mh, rows] = np.maximum(new_high[mh, rows], np.maximum(new_open[mh, rows], new_close[mh, rows]))
    new_low[ml, rows] = np.minimum(new_low[ml, rows], np.minimum(new_open[ml, rows], new_close[ml, rows]))
    out = []
    for source, new in ((open_, new_open), (high, new_high), (low, new_low), (close, new_close)):
        arr = source.copy()
        arr[:, cols] = new
        out.append(arr)
    return tuple(out)


def _fold(y: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> np.ndarray:
    """Reflect values outside [lo, hi] back inside (a triangle wave), so a path that
    overshoots turns around instead of sitting flat on the limit."""
    width = np.maximum(hi - lo, 1e-12)
    z = np.mod(y - lo, 2 * width)
    return lo + np.where(z <= width, z, 2 * width - z)


def _pin_volume(volume: np.ndarray, real: RealDay) -> np.ndarray:
    """Scale each symbol's minute volumes so the day totals the real volume exactly
    (the rounding left over goes to the minutes with the largest fractions)."""
    out = volume.copy()
    for i in np.flatnonzero(real.has & (real.volume > 0)):
        target = round(float(real.volume[i]))
        total = volume[:, i].sum()
        if target <= 0:
            continue
        weights = volume[:, i] / total if total > 0 else np.full(len(volume), 1.0 / len(volume))
        exact = weights * target
        scaled = np.floor(exact)
        short = int(target - scaled.sum())
        if short > 0:
            scaled[np.argsort(exact - scaled)[::-1][:short]] += 1
        out[:, i] = scaled
    return out


def earnings_days(spec: SymbolSpec, start: date, end: date) -> list[date]:
    """The sessions in [start, end] that open with `spec`'s earnings move."""
    if spec.earnings is None:
        return []
    month, dom = spec.earnings
    out = []
    for year in range(start.year, end.year + 1):
        for m in range(month + 1, 13, 3):
            day = _earnings_session(year, m, dom)
            if start <= day <= end:
                out.append(day)
    return out


def dividend_days(spec: SymbolSpec, start: date, end: date) -> list[date]:
    """The sessions in [start, end] on which `spec` goes ex-dividend."""
    out = []
    for year in range(start.year, end.year + 1):
        for m in spec.dividend_months:
            day = _earnings_session(year, m, spec.dividend_day)
            if start <= day <= end:
                out.append(day)
    return out


def _earnings_session(year: int, month: int, dom: int) -> date:
    target = date(year, month, dom)
    return target if is_trading_day(target) else next_trading_day(target)


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


@functools.cache
def _flow_mean(a: float = VOLUME_FLOW_SIZE) -> float:
    """E[exp(a|s| - b s)] for a unit normal s (the mean of the order-flow factor on volume)."""
    b = VOLUME_FLOW_SELL
    cdf = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))  # noqa: E731
    return math.exp((a - b) ** 2 / 2) * cdf(a - b) + math.exp((a + b) ** 2 / 2) * cdf(a + b)


_DOW_MEAN = math.log(sum(math.exp(x) for x in VOLUME_DOW) / len(VOLUME_DOW))


def _start_from(saved: list) -> _DayStart:
    prev_close, regime, *rest = saved
    variance = rest[0] if rest else 1.0
    own = rest[1] if len(rest) > 1 and rest[1] is not None else None
    shock = rest[2] if len(rest) > 2 else 0.0
    state = rest[3] if len(rest) > 3 else 0
    z = rest[4] if len(rest) > 4 else None
    phase = rest[5] if len(rest) > 5 else 0
    volume_state = rest[6] if len(rest) > 6 else None
    return _DayStart(np.array(prev_close), regime, variance, None if own is None else np.array(own), shock, state, None if z is None else np.array(z), phase, None if volume_state is None else np.array(volume_state))


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
