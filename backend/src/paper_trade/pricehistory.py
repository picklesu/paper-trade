"""Real daily closes, so a market started in the past opens near the real prices of
that time (Phase 8c).

With a Tiingo key (TIINGO_API_KEY in the environment or `backend/.env`), each
symbol's whole daily history is fetched once (about 160 KB; the free plan allows
50 requests an hour) and saved. The closes are the prices of the day, not adjusted
for later splits. Where a symbol has no close that early, its first real close is
brought back by the symbol's expected return instead.
"""

import math
import sqlite3
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from paper_trade.realprices import ENV_FILE, FetchError, _env_value, request_json
from paper_trade.store import Store

URL = "https://api.tiingo.com/tiingo/daily/{symbol}/prices"
KEY_NAME = "TIINGO_API_KEY"
FIRST_DAY = "1980-01-01"
RETRY_AFTER = timedelta(hours=1)  # after a failed fetch (the free plan's hourly limit)
DAYS_PER_YEAR = 365.25


def tiingo_key(env_file: Path = ENV_FILE) -> str | None:
    return _env_value(KEY_NAME, env_file)


def parse_closes(body: object) -> list[tuple[date, float]]:
    """(day, close) pairs, oldest first, from Tiingo's daily prices."""
    if not isinstance(body, list):
        raise FetchError("Tiingo's answer wasn't a list of prices")
    closes = []
    for row in body:
        if isinstance(row, dict) and row.get("date") and row.get("close"):
            closes.append((date.fromisoformat(row["date"][:10]), float(row["close"])))
    return closes


def fetch_closes(symbol: str, key: str) -> list[tuple[date, float]]:
    """One symbol's whole daily history (safe to run in a worker thread)."""
    query = f"?startDate={FIRST_DAY}&columns=date,close"
    body, _ = request_json(
        URL.format(symbol=symbol.replace(".", "-").lower()) + query,
        {"Authorization": f"Token {key}"},
        timeout=60,
        service="Tiingo",
    )
    return parse_closes(body)


def back_cast(price: float, from_day: date, to_day: date, drift: float) -> float:
    """What a price on `from_day` was worth on the earlier `to_day`, if the stock
    grew at its expected return (`drift`, a year) in between."""
    return price * math.exp(-drift * (from_day - to_day).days / DAYS_PER_YEAR)


class PriceHistory:
    def __init__(
        self,
        store: Store,
        key: str | None,
        fetch: Callable[[str, str], list[tuple[date, float]]] | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.store = store
        self.key = key
        self._fetch = fetch
        self._now = now
        self._db: sqlite3.Connection = store.connection
        self._first_days: dict[str, date | None] = {}
        self._db.execute("CREATE TABLE IF NOT EXISTS closes (symbol TEXT NOT NULL, day TEXT NOT NULL, close REAL NOT NULL, PRIMARY KEY (symbol, day))")

    @property
    def configured(self) -> bool:
        return bool(self.key)

    @property
    def fetched(self) -> set[str]:
        return set(self.store.get("close_history", {}))

    def due(self, symbols: list[str]) -> list[str]:
        """Symbols whose history hasn't been fetched yet (nothing, a while after a failure)."""
        retry = self.store.get("close_history_retry")
        if retry and self._now() < datetime.fromisoformat(retry):
            return []
        have = self.fetched
        return [s for s in symbols if s not in have]

    def fetch(self, symbol: str) -> list[tuple[date, float]]:
        """One request (safe in a worker thread: it doesn't touch the store)."""
        assert self.key
        return (self._fetch or fetch_closes)(symbol, self.key)

    def record(self, symbol: str, closes: list[tuple[date, float]]) -> None:
        with self._db:
            self._db.executemany(
                "INSERT OR REPLACE INTO closes (symbol, day, close) VALUES (?, ?, ?)",
                [(symbol, day.isoformat(), close) for day, close in closes],
            )
        fetched = self.store.get("close_history", {})
        fetched[symbol] = self._now().isoformat()
        self.store.set("close_history", fetched)
        self.store.set("close_history_error", None)
        self._first_days.pop(symbol, None)

    def failed(self, message: str) -> None:
        self.store.set("close_history_error", {"message": message, "at": self._now().isoformat()})
        self.store.set("close_history_retry", (self._now() + RETRY_AFTER).isoformat())

    def close_on_or_before(self, symbol: str, day: date) -> tuple[date, float] | None:
        row = self._db.execute(
            "SELECT day, close FROM closes WHERE symbol = ? AND day <= ? ORDER BY day DESC LIMIT 1", (symbol, day.isoformat())
        ).fetchone()
        return (date.fromisoformat(row[0]), row[1]) if row else None

    def first_close(self, symbol: str) -> tuple[date, float] | None:
        row = self._db.execute("SELECT day, close FROM closes WHERE symbol = ? ORDER BY day LIMIT 1", (symbol,)).fetchone()
        return (date.fromisoformat(row[0]), row[1]) if row else None

    def listed_from(self, symbol: str) -> date | None:
        """The day of the symbol's first real close, or None while that isn't known
        (its history isn't saved yet): it then counts as always listed."""
        if symbol not in self._first_days:
            first = self.first_close(symbol) if symbol in self.fetched else None
            self._first_days[symbol] = first[0] if first else None
        return self._first_days[symbol]

    def price_on(self, symbol: str, day: date, drift: float) -> float | None:
        """The close on `day` (or the last one before it); before the symbol's first
        close, that close brought back by `drift`. None if nothing is saved."""
        if (found := self.close_on_or_before(symbol, day)) is not None:
            return found[1]
        if (first := self.first_close(symbol)) is not None:
            return back_cast(first[1], first[0], day, drift)
        return None

    def status(self, symbols: list[str]) -> dict:
        error = self.store.get("close_history_error")
        return {
            "configured": self.configured,
            "available": len(self.fetched & set(symbols)),
            "total": len(symbols),
            "last_error": error["message"] if error else None,
        }
