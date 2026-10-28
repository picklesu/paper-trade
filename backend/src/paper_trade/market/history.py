"""Price bars for charts.

Finished sessions are stored as 30-minute bars (13 per session per symbol),
which cover 30m, 1h, 1D and 1W charts for any range quickly. 1m, 5m and 15m
bars are computed on request from the engine's reproducible per-second paths,
over a limited look-back. Today's bars always come live from the engine.

Bars are [time, open, high, low, close, volume] with time in Unix seconds at
the bar's start; lists are oldest first.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, timedelta

import numpy as np

from paper_trade.market.calendar import (
    market_day,
    next_trading_day,
    previous_trading_day,
    session_open,
    session_seconds,
)
from paper_trade.market.engine import MarketEngine

STORED_MINUTES = 30
INTERVALS = ("1m", "5m", "15m", "30m", "1h", "1D", "1W")
FINE_MINUTES = {"1m": 1, "5m": 5, "15m": 15}
# How many sessions back the finer bars go (they're computed on request).
FINE_LOOKBACK_DAYS = {"1m": 60, "5m": 252, "15m": 252}
MAX_COUNT = 2000
BATCH_ROWS = 13 * 10 * 100  # about 100 sessions of 10 symbols per commit

Bar = list[float]


def aggregate(bars: np.ndarray, k: int) -> np.ndarray:
    """Combine consecutive groups of `k` bars along axis 0 (the last group may be short)."""
    if k == 1:
        return bars
    idx = np.arange(0, bars.shape[0], k)
    last = np.minimum(idx + k, bars.shape[0]) - 1
    out = np.empty((len(idx),) + bars.shape[1:])
    out[..., 0] = bars[idx, ..., 0]
    out[..., 1] = np.maximum.reduceat(bars[..., 1], idx, axis=0)
    out[..., 2] = np.minimum.reduceat(bars[..., 2], idx, axis=0)
    out[..., 3] = bars[last, ..., 3]
    out[..., 4] = np.add.reduceat(bars[..., 4], idx, axis=0)
    return out


def _row(t: datetime | float, b) -> Bar:
    ts = t.timestamp() if isinstance(t, datetime) else t
    return [int(ts), round(float(b[0]), 2), round(float(b[1]), 2), round(float(b[2]), 2), round(float(b[3]), 2), int(b[4])]


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


class History:
    def __init__(self, db: sqlite3.Connection, engine: MarketEngine) -> None:
        self.db = db
        self.engine = engine
        self._pending: list[tuple] | None = None  # rows held back during batch()
        db.execute(
            "CREATE TABLE IF NOT EXISTS bars30 (symbol TEXT, day TEXT, i INTEGER,"
            " o REAL, h REAL, l REAL, c REAL, v REAL, PRIMARY KEY (symbol, day, i))"
        )

    # ----- storing finished sessions ------------------------------------------------

    def record(self, day: date) -> None:
        """Store a finished session's 30-minute bars (idempotent)."""
        bars = aggregate(self.engine.minute_bars(day), STORED_MINUTES)
        rows = [
            (spec.symbol, day.isoformat(), i, *map(float, bars[i, j]))
            for j, spec in enumerate(self.engine.specs)
            for i in range(bars.shape[0])
        ]
        if self._pending is not None:
            self._pending += rows
            if len(self._pending) >= BATCH_ROWS:
                self.flush()
        else:
            self._write(rows)

    @contextmanager
    def batch(self) -> Iterator[None]:
        """Hold recorded sessions and write them in a few large commits (for long
        fast-forwards and history builds), instead of one commit per session."""
        outer = self._pending is not None
        if not outer:
            self._pending = []
        try:
            yield
        finally:
            if not outer:
                self.flush()
                self._pending = None

    def flush(self) -> None:
        if self._pending:
            self._write(self._pending)
            self._pending = []

    def _write(self, rows: list[tuple]) -> None:
        self.db.executemany("INSERT OR REPLACE INTO bars30 VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)

    def stored_days(self) -> set[date]:
        self.flush()
        return {date.fromisoformat(d) for (d,) in self.db.execute("SELECT DISTINCT day FROM bars30")}

    def delete_from(self, day: date) -> None:
        """Forget sessions from `day` on (a new future regenerates them)."""
        self.flush()
        self.db.execute("DELETE FROM bars30 WHERE day >= ?", (day.isoformat(),))

    def clear(self) -> None:
        if self._pending is not None:
            self._pending = []
        self.db.execute("DELETE FROM bars30")

    def missing_days(self) -> list[date]:
        """Finished sessions (origin up to yesterday) with no stored bars."""
        have = self.stored_days()
        today = market_day(self.engine.time)
        day, out = market_day(self.engine.origin), []
        while day < today:
            if day not in have:
                out.append(day)
            day = next_trading_day(day)
        return out

    # ----- queries ---------------------------------------------------------------------

    def bars(self, symbol: str, interval: str, end: datetime | None = None, count: int = 500) -> dict:
        """Up to `count` bars before `end` (default: now, including the live bar),
        oldest first, and whether older bars exist."""
        if interval not in INTERVALS:
            raise ValueError(f"interval must be one of {', '.join(INTERVALS)}")
        count = max(1, min(count, MAX_COUNT))
        col = self.engine.symbol_index(symbol)
        now = self.engine.time
        cutoff = min(end, now) if end else now
        if interval in FINE_MINUTES:
            bars, more = self._fine(col, interval, cutoff, include_cutoff=end is None, count=count)
        else:
            bars, more = self._stored(symbol, col, interval, cutoff, include_cutoff=end is None, count=count)
        return {"bars": bars, "more": more}

    def _today(self, col: int, minutes: int | None) -> list[tuple[datetime, np.ndarray]]:
        """Live bars for the current session: `minutes` per bar, or one bar for the day."""
        now = self.engine.time
        day = market_day(now)
        mb = self.engine.minute_bars(day, through=int(session_seconds(now)))[:, col, :]
        open_ = session_open(day)
        if minutes is None:
            return [(open_, aggregate(mb, mb.shape[0])[0])]
        agg = aggregate(mb, minutes)
        return [(open_ + timedelta(minutes=minutes * i), agg[i]) for i in range(agg.shape[0])]

    def _fine(self, col: int, interval: str, cutoff: datetime, include_cutoff: bool, count: int) -> tuple[list[Bar], bool]:
        k = FINE_MINUTES[interval]
        today = market_day(self.engine.time)
        origin_day = market_day(self.engine.origin)
        oldest_allowed = today
        for _ in range(FINE_LOOKBACK_DAYS[interval] - 1):
            if oldest_allowed <= origin_day:
                break
            oldest_allowed = previous_trading_day(oldest_allowed)

        out: list[Bar] = []
        day = market_day(cutoff)
        while day >= oldest_allowed and len(out) < count:
            if day == today:
                rows = [_row(t, b) for t, b in self._today(col, k)]
            else:
                agg = aggregate(self.engine.minute_bars(day)[:, col, :], k)
                open_ = session_open(day)
                rows = [_row(open_ + timedelta(minutes=k * i), agg[i]) for i in range(agg.shape[0])]
            limit = cutoff.timestamp()
            rows = [r for r in rows if r[0] < limit or (include_cutoff and r[0] <= limit)]
            out = rows + out
            day = previous_trading_day(day)
        more = day >= oldest_allowed or len(out) > count
        return out[-count:], more

    def _stored(
        self, symbol: str, col: int, interval: str, cutoff: datetime, include_cutoff: bool, count: int
    ) -> tuple[list[Bar], bool]:
        today = market_day(self.engine.time)
        origin_day = market_day(self.engine.origin)
        last_day = market_day(cutoff)
        per_day = {"30m": 13, "1h": 7, "1D": 1, "1W": 0.2}[interval]
        span = int(count / per_day) + 6
        first_day = last_day - timedelta(days=span * 7 // 5 + 7)
        if interval == "1W":
            first_day = week_start(first_day)  # whole weeks only, so pages never split one

        self.flush()  # include anything held back by a batch in progress
        # Stored sessions (never today's: it's still in progress, maybe after a replay).
        stored_end = min(last_day, today - timedelta(days=1))
        rows = self.db.execute(
            "SELECT day, i, o, h, l, c, v FROM bars30 WHERE symbol = ? AND day >= ? AND day <= ? ORDER BY day, i",
            (symbol, first_day.isoformat(), stored_end.isoformat()),
        ).fetchall()
        days: dict[date, list] = {}
        for d, i, *ohlcv in rows:
            days.setdefault(date.fromisoformat(d), []).append(ohlcv)
        sessions: list[tuple[date, np.ndarray]] = [(d, np.array(v)) for d, v in days.items()]
        if last_day >= today:
            mb = self.engine.minute_bars(today, through=int(session_seconds(self.engine.time)))[:, col, :]
            sessions.append((today, aggregate(mb, STORED_MINUTES)))

        out: list[Bar] = []
        if interval in ("30m", "1h"):
            k = 1 if interval == "30m" else 2
            for d, b30 in sessions:
                agg = aggregate(b30, k)
                open_ = session_open(d)
                out += [_row(open_ + timedelta(minutes=30 * k * i), agg[i]) for i in range(agg.shape[0])]
        elif interval == "1D":
            out = [_row(session_open(d), aggregate(b30, b30.shape[0])[0]) for d, b30 in sessions]
        else:
            weeks: dict[date, list[tuple[date, np.ndarray]]] = {}
            for d, b30 in sessions:
                weeks.setdefault(week_start(d), []).append((d, b30))
            for group in weeks.values():
                stacked = np.concatenate([b for _, b in group])
                out.append(_row(session_open(group[0][0]), aggregate(stacked, stacked.shape[0])[0]))

        limit = cutoff.timestamp()
        out = [r for r in out if r[0] < limit or (include_cutoff and r[0] <= limit)]
        more = first_day > origin_day or len(out) > count
        return out[-count:], more
