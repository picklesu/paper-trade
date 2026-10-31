"""The real daily bars the engine replays (8e): per symbol and day, split-adjusted
open, high, low, close and volume, and the dividends. Built once at startup from
`PriceHistory`; the engine never touches the database.
"""

import bisect
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np

from paper_trade.market.calendar import RULE_HOLIDAYS
from paper_trade.pricehistory import Bar


@dataclass(frozen=True)
class RealDay:
    """One session's real bars, a column per symbol; `has` is False where a symbol has none."""

    has: np.ndarray  # (N,) bool
    open: np.ndarray  # (N,) NaN where there is no bar
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    dividend: np.ndarray  # (N,) cash a share going ex that day (0 if none)


class RealBarsGone(ValueError):
    """A saved market replayed real bars that are no longer saved."""


class RealBars:
    def __init__(self, symbols: Sequence[str], bars: dict[str, list[Bar]], vix: Sequence[tuple[date, float]] = ()) -> None:
        self.symbols = tuple(symbols)
        good = [(d, v) for d, v in vix if isinstance(v, (int, float)) and math.isfinite(v) and v > 0]
        self._vix_days = [d for d, _ in good]
        self._vix = [v for _, v in good]
        self._adjusted: dict[tuple[int, int], np.ndarray] = {}
        self._volume_series: dict[int, tuple[list[date], np.ndarray, np.ndarray]] = {}
        self._bars: list[dict[date, Bar]] = [{b.day: b for b in bars.get(s, [])} for s in self.symbols]
        self._first: list[date | None] = [min(d, default=None) for d in self._bars]
        days = [max(d) for d in self._bars if d]
        self.last_day: date | None = max(days, default=None)  # the newest bar of any symbol
        self._dividends = [sorted((d, b.dividend) for d, b in bars_.items() if b.dividend > 0) for bars_ in self._bars]
        self._days: dict[date, RealDay | None] = {}

    @classmethod
    def from_history(cls, history, symbols: Sequence[str], macro=None) -> "RealBars | None":
        """The saved bars of `symbols` (None while none are saved), and the real VIX from `macro`."""
        bars = {s: history.bars(s) for s in symbols if s in history.fetched}
        real = cls(symbols, bars, macro.series("VIXCLS") if macro else ())
        return real if real.last_day else None

    def day(self, d: date) -> RealDay | None:
        """The real bars of session `d` (None if no symbol has one)."""
        if d not in self._days:
            self._days[d] = self._build_day(d)
        return self._days[d]

    def _build_day(self, d: date) -> RealDay | None:
        found = [b.get(d) for b in self._bars]
        has = np.array([x is not None for x in found])
        if not has.any():
            return None

        def col(name: str) -> np.ndarray:
            return np.array([getattr(x, name) if x else np.nan for x in found])

        dividend = np.array([x.dividend if x else 0.0 for x in found])
        return RealDay(has, col("open"), col("high"), col("low"), col("close"), col("volume"), dividend)

    def vix_on_or_before(self, d: date) -> float | None:
        """The real VIX close of the last session on or before `d` (None before it exists)."""
        k = bisect.bisect_right(self._vix_days, d)
        return self._vix[k - 1] if k else None

    def spike_near(self, i: int, center: date, window_days: int = 10, min_ratio: float = 1.1, market: int | None = None) -> date | None:
        """The day within `window_days` of `center` on which symbol `i` traded the most
        compared with its median of the 20 sessions before, weighted by how wide the day's
        range was (an earnings reaction), if the score is at least `min_ratio`."""
        if market is not None and market != i:  # busy because the whole market was does not count
            days, _, raw = self._series(i)
            if (i, market) not in self._adjusted:
                m_days, _, m_ratio = self._series(market)
                lookup = dict(zip(m_days, m_ratio))
                self._adjusted[(i, market)] = np.array([r / max(lookup.get(d, 1.0), 1.0) for d, r in zip(days, raw)])
            ratio = self._adjusted[(i, market)]
            return self._peak(days, ratio, center, window_days, min_ratio)
        days, _, ratio = self._series(i)
        return self._peak(days, ratio, center, window_days, min_ratio)

    def _series(self, i: int) -> tuple[list[date], np.ndarray, np.ndarray]:
        if i not in self._volume_series:
            bars = sorted(self._bars[i].values(), key=lambda b: b.day)
            days = [b.day for b in bars]
            volume = np.array([b.volume for b in bars], dtype=float)
            span = np.array([(b.high - b.low) / b.close if b.close > 0 else 0.0 for b in bars])
            ratio = np.zeros(len(bars))
            for j in range(5, len(bars)):
                base = float(np.median(volume[max(0, j - 20) : j]))
                width = float(np.median(span[max(0, j - 20) : j]))
                if base > 0 and width > 0:  # a reaction is a busy day and a wide one
                    ratio[j] = volume[j] / base * math.sqrt(max(span[j] / width, 0.25))
            self._volume_series[i] = (days, volume, ratio)
        return self._volume_series[i]

    @staticmethod
    def _peak(days: list[date], ratio: np.ndarray, center: date, window_days: int, min_ratio: float) -> date | None:
        lo, hi = bisect.bisect_left(days, center - timedelta(days=window_days)), bisect.bisect_right(days, center + timedelta(days=window_days))
        if lo >= hi:
            return None
        j = lo + int(np.argmax(ratio[lo:hi]))
        return days[j] if ratio[j] >= min_ratio else None

    def first_day(self, i: int) -> date | None:
        return self._first[i]

    def close_before(self, i: int, d: date) -> float | None:
        """Symbol `i`'s last real close before `d` (None before its first bar)."""
        first = self._first[i]
        if first is None or first >= d:
            return None
        bars = self._bars[i]
        day = d - timedelta(days=1)
        while day not in bars:
            day -= timedelta(days=1)
            if day < first:
                return None
        return bars[day].close

    def dividends(self, i: int, start: date, end: date) -> list[tuple[date, float]]:
        """Symbol `i`'s real ex-dividend dates in [start, end] with cash a share (split-adjusted)."""
        rows = self._dividends[i]
        return rows[bisect.bisect_left(rows, (start, -1.0)) : bisect.bisect_right(rows, (end, float("inf")))]

    def covers(self, i: int, d: date) -> bool:
        """Whether real data stands for symbol `i` on `d` (from its first bar to the newest day)."""
        first = self._first[i]
        return first is not None and self.last_day is not None and first <= d <= self.last_day

    def closures(self) -> tuple[set[date], set[date]]:
        """Weekdays the rules call open that no symbol traded (one-off closures such as
        September 2001 or Sandy), and rule holidays that did trade."""
        traded = {d for bars in self._bars for d in bars}
        first = min((f for f in self._first if f), default=None)
        if first is None or self.last_day is None:
            return set(), set()
        closed, opened = set(), set()
        d = first
        while d < self.last_day:  # the newest day itself may be a partial fetch
            if d.weekday() < 5:
                if d in RULE_HOLIDAYS and d in traded:
                    opened.add(d)
                elif d not in RULE_HOLIDAYS and d not in traded:
                    closed.add(d)
            d += timedelta(days=1)
        return closed, opened
