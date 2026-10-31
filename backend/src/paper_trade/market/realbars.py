"""The real daily bars the engine replays (8e): per symbol and day, split-adjusted
open, high, low, close and volume, and the dividends. Built once at startup from
`PriceHistory`; the engine never touches the database.
"""

import bisect
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


class RealBarsGone(ValueError):
    """A saved market replayed real bars that are no longer saved."""


class RealBars:
    def __init__(self, symbols: Sequence[str], bars: dict[str, list[Bar]]) -> None:
        self.symbols = tuple(symbols)
        self._bars: list[dict[date, Bar]] = [{b.day: b for b in bars.get(s, [])} for s in self.symbols]
        self._first: list[date | None] = [min(d, default=None) for d in self._bars]
        days = [max(d) for d in self._bars if d]
        self.last_day: date | None = max(days, default=None)  # the newest bar of any symbol
        self._dividends = [sorted((d, b.dividend) for d, b in bars_.items() if b.dividend > 0) for bars_ in self._bars]
        self._days: dict[date, RealDay | None] = {}

    @classmethod
    def from_history(cls, history, symbols: Sequence[str]) -> "RealBars | None":
        """The saved bars of `symbols` (None while none are saved)."""
        bars = {s: history.bars(s) for s in symbols if s in history.fetched}
        real = cls(symbols, bars)
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

        return RealDay(has, col("open"), col("high"), col("low"), col("close"), col("volume"))

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
