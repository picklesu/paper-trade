"""Price bars for charts.

Finished sessions are stored as 30-minute bars (13 per session per symbol),
which cover 30m, 1h, 1D and 1W charts for any range quickly. 1m, 5m and 15m
bars are computed on request from the engine's reproducible one-minute sessions,
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
    is_trading_day,
    market_day,
    next_trading_day,
    previous_trading_day,
    session_close,
    session_open,
    session_seconds,
)
from paper_trade.market.engine import BREAKER_LEVELS, MarketEngine

STORED_MINUTES = 30
VIX_SYMBOL = "VIX"  # an index stored beside the symbols, in 30-minute bars only
INTERVALS = ("1m", "5m", "15m", "30m", "1h", "1D", "1W")
FINE_MINUTES = {"1m": 1, "5m": 5, "15m": 15}
# How many sessions back the finer bars go (they're computed on request).
FINE_LOOKBACK_DAYS = {"1m": 60, "5m": 252, "15m": 252}
MAX_COUNT = 2000
AVG_VOLUME_DAYS = 30  # sessions in the average daily volume
BATCH_ROWS = 50_000  # rows per commit: about 100 sessions of 40 symbols

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

BREAKER_FIRST_LEVEL = BREAKER_LEVELS[0]


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
        # For queries by date across symbols (quote stats).
        db.execute("CREATE INDEX IF NOT EXISTS bars30_day ON bars30 (day)")

    # ----- storing finished sessions ------------------------------------------------

    def record(self, day: date) -> None:
        """Store a finished session's 30-minute bars (idempotent)."""
        self._record_bars(day, aggregate(self.engine.minute_bars(day), STORED_MINUTES), self.engine.vix_bars(day))

    def backfill_iter(self, days: int) -> Iterator[date]:
        """Generate the history before the market's origin and store its bars,
        yielding after each session. Sessions are generated at unit scale (see
        `MarketEngine.backfill_iter`), so their bars are held and stored once the
        final scale is known; an interrupted build stores nothing."""
        held = []
        for day in self.engine.backfill_iter(days):
            held.append((day, aggregate(self.engine.minute_bars(day), STORED_MINUTES), self.engine.vix_bars(day)))
            yield day
        for day, bars, vix in held:
            bars[..., :4] *= self.engine.backfill_scale_on(day)[:, None]
            self._record_bars(day, bars, vix)  # the index needs no scale

    def _record_bars(self, day: date, bars: np.ndarray, vix: np.ndarray | None = None) -> None:
        rows = [
            (spec.symbol, day.isoformat(), i, *map(float, bars[i, j]))
            for j, spec in enumerate(self.engine.specs)
            for i in range(bars.shape[0])
        ]
        if vix is not None:
            rows += [(VIX_SYMBOL, day.isoformat(), i, *map(float, vix[i])) for i in range(vix.shape[0])]
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
        # One transaction: the connection autocommits, which would otherwise commit every row.
        self.db.execute("BEGIN")
        try:
            self.db.executemany("INSERT OR REPLACE INTO bars30 VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        self.db.execute("COMMIT")

    def halts_between(self, start: date, end: date) -> list[dict]:
        """Circuit-breaker halts on stored sessions from `start` to `end`. A halt needs SPY to fall
        at least 7% below the previous close, so only such days (found from the stored bars,
        whose lows keep the dip) are regenerated to read their halts off."""
        if not any(s.symbol == "SPY" for s in self.engine.specs):
            return []
        self.flush()
        today = market_day(self.engine.time)
        stop = min(end, today - timedelta(days=1))
        rows = self.db.execute(
            "SELECT day, i, l, c FROM bars30 WHERE symbol = 'SPY' AND day >= ? AND day <= ? ORDER BY day, i",
            ((start - timedelta(days=7)).isoformat(), stop.isoformat()),
        ).fetchall()
        lows: dict[str, float] = {}
        closes: dict[str, float] = {}
        for day_s, _, low, close in rows:
            lows[day_s] = min(lows.get(day_s, low), low)
            closes[day_s] = close  # the last bar's close (rows run in order)
        out: list[dict] = []
        previous = None  # the previous stored session's close
        for day_s in sorted(lows):
            day = date.fromisoformat(day_s)
            if previous is not None and day >= start and lows[day_s] <= previous * (1 - BREAKER_FIRST_LEVEL) * 1.0001:
                out += self.engine.halts_of(day)
            previous = closes[day_s]
        return out

    def stored_days(self) -> set[date]:
        self.flush()
        return {date.fromisoformat(d) for (d,) in self.db.execute("SELECT DISTINCT day FROM bars30")}

    def delete_from(self, day: date) -> None:
        """Forget sessions from `day` on (a new future regenerates them)."""
        self.flush()
        self.db.execute("DELETE FROM bars30 WHERE day >= ?", (day.isoformat(),))

    def delete_before(self, day: date) -> None:
        """Forget sessions before `day` (history about to be generated again)."""
        self.flush()
        self.db.execute("DELETE FROM bars30 WHERE day < ?", (day.isoformat(),))

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

    def quote_stats(self) -> dict[str, dict]:
        """Each symbol's 52-week high and low and average daily volume over the
        last AVG_VOLUME_DAYS sessions (over the days each symbol has), from finished sessions before today (the
        UI folds in today's own range). Symbols with no stored history are left out."""
        self.flush()
        today = market_day(self.engine.time)
        year_ago = (today - timedelta(weeks=52)).isoformat()
        days = [
            d
            for (d,) in self.db.execute(
                "SELECT DISTINCT day FROM bars30 WHERE day < ? ORDER BY day DESC LIMIT ?",
                (today.isoformat(), AVG_VOLUME_DAYS),
            )
        ]
        if not days:
            return {}
        ranges = self.db.execute(
            "SELECT symbol, MAX(h), MIN(l) FROM bars30 WHERE day >= ? AND day < ? GROUP BY symbol",
            (year_ago, today.isoformat()),
        )
        volumes = {
            symbol: total / count
            for symbol, total, count in self.db.execute(
                "SELECT symbol, SUM(v), COUNT(DISTINCT day) FROM bars30 WHERE day >= ? AND day < ? GROUP BY symbol",
                (days[-1], today.isoformat()),
            )
        }
        return {
            symbol: {
                "high_52w": round(high, 2),
                "low_52w": round(low, 2),
                "avg_volume": int(volumes.get(symbol, 0)),
            }
            for symbol, high, low in ranges
        }

    def bars(self, symbol: str, interval: str, end: datetime | None = None, count: int = 500) -> dict:
        """Up to `count` bars, oldest first, and whether older bars exist.

        Without `end`: the latest bars, including the live (unfinished) one. With
        `end`: only bars that finished by then, so a page never includes prices from
        after `end` (paging back with `end` = the oldest bar's start fits exactly).
        """
        if interval not in INTERVALS:
            raise ValueError(f"interval must be one of {', '.join(INTERVALS)}")
        count = max(1, min(count, MAX_COUNT))
        index = symbol == VIX_SYMBOL
        col = -1 if index else self.engine.symbol_index(symbol)
        if index and interval in FINE_MINUTES:
            interval = "30m"  # the index is sampled every half hour
        now = self.engine.time
        limit = end.timestamp() if end is not None and end < now else None
        cutoff = end if limit is not None else now
        if interval in FINE_MINUTES:
            timed, more = self._fine(col, interval, cutoff, count)
        else:
            timed, more = self._stored(symbol, col, interval, cutoff, count)
        bars = [row for row, ends in timed if limit is None or ends <= limit]
        more = more or len(bars) > count
        return {"bars": bars[-count:], "more": more}

    def _session_bars(self, day: date, col: int, minutes: int) -> list[tuple[Bar, float]]:
        """`minutes`-minute bars for a session (live through now if it's today), with end times."""
        live = day == market_day(self.engine.time)
        mb = self.engine.minute_bars(day, through=int(session_seconds(self.engine.time)) if live else None)
        return self._timed(day, aggregate(mb[:, col, :], minutes), minutes)

    @staticmethod
    def _timed(day: date, agg: np.ndarray, minutes: int) -> list[tuple[Bar, float]]:
        open_, close = session_open(day), session_close(day).timestamp()
        out = []
        for i in range(agg.shape[0]):
            start = open_ + timedelta(minutes=minutes * i)
            out.append((_row(start, agg[i]), min(start.timestamp() + minutes * 60, close)))
        return out

    def _fine(self, col: int, interval: str, cutoff: datetime, count: int) -> tuple[list[tuple[Bar, float]], bool]:
        if col < 0:
            raise ValueError("an index has no bars finer than 30 minutes")
        k = FINE_MINUTES[interval]
        today = market_day(self.engine.time)
        origin_day = market_day(self.engine.origin)
        oldest_allowed = today
        for _ in range(FINE_LOOKBACK_DAYS[interval] - 1):
            if oldest_allowed <= origin_day:
                break
            oldest_allowed = previous_trading_day(oldest_allowed)

        out: list[tuple[Bar, float]] = []
        day = market_day(cutoff)
        if not is_trading_day(day):
            day = previous_trading_day(day)
        while day >= oldest_allowed and len(out) <= count:
            out = [b for b in self._session_bars(day, col, k) if b[0][0] < cutoff.timestamp() or day == today] + out
            day = previous_trading_day(day)
        return out, day >= oldest_allowed

    def _stored(
        self, symbol: str, col: int, interval: str, cutoff: datetime, count: int
    ) -> tuple[list[tuple[Bar, float]], bool]:
        today = market_day(self.engine.time)
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
            through = int(session_seconds(self.engine.time))
            if symbol == VIX_SYMBOL:
                live = self.engine.vix_bars(today, through=through)
                if live is not None:
                    sessions.append((today, live))
            else:
                mb = self.engine.minute_bars(today, through=through)[:, col, :]
                sessions.append((today, aggregate(mb, STORED_MINUTES)))

        out: list[tuple[Bar, float]] = []
        if interval in ("30m", "1h"):
            k = 1 if interval == "30m" else 2
            for d, b30 in sessions:
                out += self._timed(d, aggregate(b30, k), 30 * k)
        elif interval == "1D":
            out = [(_row(session_open(d), aggregate(b30, b30.shape[0])[0]), session_close(d).timestamp()) for d, b30 in sessions]
        else:
            weeks: dict[date, list[tuple[date, np.ndarray]]] = {}
            for d, b30 in sessions:
                weeks.setdefault(week_start(d), []).append((d, b30))
            for group in weeks.values():
                stacked = np.concatenate([b for _, b in group])
                bar = _row(session_open(group[0][0]), aggregate(stacked, stacked.shape[0])[0])
                out.append((bar, session_close(group[-1][0]).timestamp()))

        (earliest,) = self.db.execute("SELECT MIN(day) FROM bars30 WHERE symbol = ?", (symbol,)).fetchone()
        more = earliest is not None and date.fromisoformat(earliest) < first_day
        return out, more
