"""Real daily bars, so a market started in the past opens near the real prices of
that time (8c) and, from 9, replays the real days.

With a Tiingo key (TIINGO_API_KEY in the environment or `backend/.env`), each
symbol's whole daily history (open, high, low, close, volume, dividend, split
factor) is fetched once (about 0.5 MB; the free plan allows 50 requests an hour)
and saved. Prices and volumes are **adjusted for splits** (not for dividends), so
they continue into today's prices and share counts: a stock that split 10-for-1
shows its old prices a tenth as high. Where a symbol has no bar that early, its
first real close is brought back by the symbol's expected return instead.
"""

import math
import sqlite3
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import NamedTuple

from paper_trade.realprices import ENV_FILE, FetchError, _env_value, request_json
from paper_trade.store import Store

URL = "https://api.tiingo.com/tiingo/daily/{symbol}/prices"
KEY_NAME = "TIINGO_API_KEY"
FIRST_DAY = "1980-01-01"
DAYS_PER_YEAR = 365.25


def tiingo_key(env_file: Path = ENV_FILE) -> str | None:
    return _env_value(KEY_NAME, env_file)


class Bar(NamedTuple):
    """One real trading day, split-adjusted. `dividend` is cash a share (also adjusted);
    `split` is that day's split factor (2 for a 2-for-1), 1 if none."""

    day: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    dividend: float = 0.0
    split: float = 1.0


def parse_bars(body: object) -> list[Bar]:
    """Bars oldest first, split-adjusted, from Tiingo's raw (as-traded) daily prices.
    A bar before a split is divided by the factor (its volume multiplied), so every
    price is in today's shares. Rows without a close are left out."""
    if not isinstance(body, list):
        raise FetchError("Tiingo's answer wasn't a list of prices")
    rows = []
    for row in body:
        if isinstance(row, dict) and row.get("date") and row.get("close"):
            rows.append(row)
    rows.sort(key=lambda r: r["date"])
    bars: list[Bar] = []
    factor = 1.0  # the product of the splits after the bar
    for row in reversed(rows):
        split = float(row.get("splitFactor") or 1.0) or 1.0
        close = float(row["close"])
        bars.append(
            Bar(
                date.fromisoformat(row["date"][:10]),
                float(row.get("open") or close) / factor,
                float(row.get("high") or close) / factor,
                float(row.get("low") or close) / factor,
                close / factor,
                float(row.get("volume") or 0) * factor,
                float(row.get("divCash") or 0) / factor,
                split,
            )
        )
        factor *= split
    bars.reverse()
    return bars


def fetch_bars(symbol: str, key: str, since: date | None = None) -> list[Bar]:
    """One symbol's daily history from `since` (default: all of it); safe to run in a worker thread."""
    query = f"?startDate={since.isoformat() if since else FIRST_DAY}&columns=date,open,high,low,close,volume,divCash,splitFactor"
    body, _ = request_json(
        URL.format(symbol=symbol.replace(".", "-").lower()) + query,
        {"Authorization": f"Token {key}"},
        timeout=60,
        service="Tiingo",
    )
    return parse_bars(body)


def back_cast(price: float, from_day: date, to_day: date, drift: float) -> float:
    """What a price on `from_day` was worth on the earlier `to_day`, if the stock
    grew at its expected return (`drift`, a year) in between."""
    return price * math.exp(-drift * (from_day - to_day).days / DAYS_PER_YEAR)


class PriceHistory:
    def __init__(
        self,
        store: Store,
        key: str | None,
        fetch: Callable[[str, str], list[Bar]] | None = None,
        fetch_since: Callable[[str, str, date], list[Bar]] | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.store = store
        self.key = key
        self._fetch = fetch
        self._fetch_since = fetch_since
        self._now = now
        self._db: sqlite3.Connection = store.connection
        self._first_days: dict[str, date | None] = {}
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS daily (symbol TEXT NOT NULL, day TEXT NOT NULL, open REAL NOT NULL, high REAL NOT NULL, "
            "low REAL NOT NULL, close REAL NOT NULL, volume REAL NOT NULL, dividend REAL NOT NULL, split REAL NOT NULL, "
            "PRIMARY KEY (symbol, day))"
        )
        # 8c saved unadjusted closes only; the bars replace them (and are fetched again).
        self._db.execute("DROP TABLE IF EXISTS closes")

    @property
    def configured(self) -> bool:
        return bool(self.key)

    @property
    def fetched(self) -> set[str]:
        return set(self.store.get("daily_history", {}))

    def due(self, symbols: list[str]) -> list[str]:
        """Symbols whose history hasn't been fetched yet."""
        have = self.fetched | set(self.store.get("daily_history_skipped", {}))
        return [s for s in symbols if s not in have]

    def fetch(self, symbol: str) -> list[Bar]:
        """One request (safe in a worker thread: it doesn't touch the store)."""
        assert self.key
        return (self._fetch or fetch_bars)(symbol, self.key)

    def fetch_new(self, symbol: str, last: date, last_split: float = 1.0) -> tuple[list[Bar], bool]:
        """One request for the days since `last`, the symbol's last saved bar
        (`last_day`; included, since it may have been fetched before its close); safe
        in a worker thread (it doesn't touch the store). A split among the new days changes the earlier bars' adjustment, so
        then the whole history is fetched instead (second item True)."""
        assert self.key
        bars = (self._fetch_since or fetch_bars)(symbol, self.key, last)
        if any(b.split != 1.0 and (b.day > last or b.split != last_split) for b in bars):
            return self.fetch(symbol), True
        return bars, False

    def record_new(self, symbol: str, bars: list[Bar], whole: bool) -> int:
        """Save `fetch_new`'s bars; returns how many days are new."""
        before = self.last_day(symbol)
        saved = len(self.bars(symbol))
        if whole and len(bars) < saved:  # history only grows: a short answer is a bad one
            raise FetchError(f"{symbol}: Tiingo returned {len(bars)} days where {saved} are saved; kept what is saved")
        self.record(symbol, bars, replace=whole)
        return sum(1 for b in bars if before is None or b.day > before)

    def record(self, symbol: str, bars: list[Bar], replace: bool = False) -> None:
        with self._db:
            if replace:
                self._db.execute("DELETE FROM daily WHERE symbol = ?", (symbol,))
            self._db.executemany(
                "INSERT OR REPLACE INTO daily (symbol, day, open, high, low, close, volume, dividend, split) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(symbol, b.day.isoformat(), b.open, b.high, b.low, b.close, b.volume, b.dividend, b.split) for b in bars],
            )
        fetched = self.store.get("daily_history", {})
        fetched[symbol] = self._now().isoformat()
        self.store.set("daily_history", fetched)
        self.store.set("daily_history_error", None)
        self._first_days.pop(symbol, None)

    def skip(self, symbol: str, message: str) -> None:
        """This symbol alone can't be fetched (Tiingo doesn't know it): don't ask again."""
        skipped = self.store.get("daily_history_skipped", {})
        skipped[symbol] = message
        self.store.set("daily_history_skipped", skipped)

    def failed(self, message: str) -> None:
        self.store.set("daily_history_error", {"message": message, "at": self._now().isoformat()})

    def close_on_or_before(self, symbol: str, day: date) -> tuple[date, float] | None:
        row = self._db.execute(
            "SELECT day, close FROM daily WHERE symbol = ? AND day <= ? ORDER BY day DESC LIMIT 1", (symbol, day.isoformat())
        ).fetchone()
        return (date.fromisoformat(row[0]), row[1]) if row else None

    def first_close(self, symbol: str) -> tuple[date, float] | None:
        row = self._db.execute("SELECT day, close FROM daily WHERE symbol = ? ORDER BY day LIMIT 1", (symbol,)).fetchone()
        return (date.fromisoformat(row[0]), row[1]) if row else None

    def listed_from(self, symbol: str) -> date | None:
        """The day of the symbol's first real close, or None while that isn't known
        (its history isn't saved yet): it then counts as always listed."""
        if symbol not in self._first_days:
            first = self.first_close(symbol) if symbol in self.fetched else None
            self._first_days[symbol] = first[0] if first else None
        return self._first_days[symbol]

    def bars(self, symbol: str, start: date | None = None, end: date | None = None) -> list[Bar]:
        """The symbol's real bars from `start` through `end` (inclusive), oldest first."""
        rows = self._db.execute(
            "SELECT day, open, high, low, close, volume, dividend, split FROM daily WHERE symbol = ? AND day >= ? AND day <= ? ORDER BY day",
            (symbol, (start or date.min).isoformat(), (end or date.max).isoformat()),
        ).fetchall()
        return [Bar(date.fromisoformat(r[0]), *r[1:]) for r in rows]

    def last_day(self, symbol: str) -> date | None:
        row = self._db.execute("SELECT MAX(day) FROM daily WHERE symbol = ?", (symbol,)).fetchone()
        return date.fromisoformat(row[0]) if row and row[0] else None

    def newest_day(self) -> str | None:
        """The latest day with a saved bar, for any symbol."""
        row = self._db.execute("SELECT MAX(day) FROM daily").fetchone()
        return row[0] if row else None

    def price_on(self, symbol: str, day: date, drift: float) -> float | None:
        """The close on `day` (or the last one before it); before the symbol's first
        close, that close brought back by `drift`. None if nothing is saved."""
        if (found := self.close_on_or_before(symbol, day)) is not None:
            return found[1]
        if (first := self.first_close(symbol)) is not None:
            return back_cast(first[1], first[0], day, drift)
        return None

    def status(self, symbols: list[str]) -> dict:
        error = self.store.get("daily_history_error")
        return {
            "configured": self.configured,
            "available": len(self.fetched & set(symbols)),
            "total": len(symbols),
            "last_error": error["message"] if error else None,
        }
