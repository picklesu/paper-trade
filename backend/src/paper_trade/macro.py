"""Real economic series from FRED (Phase 9): interest rates, the yield curve, credit
spreads, unemployment, inflation, the VIX and the NBER recession flag. They train
and start the future market model. Needs FRED_API_KEY (environment or
`backend/.env`; free from fred.stlouisfed.org). One request a series, once; later days
come only when asked for (`fetch_new`). Nothing here is needed to run the sim without a key.
"""

import sqlite3
import urllib.parse
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from paper_trade.realprices import ENV_FILE, FetchError, _env_value, request_json
from paper_trade.store import Store

URL = "https://api.stlouisfed.org/fred/series/observations"
KEY_NAME = "FRED_API_KEY"
FIRST_DAY = "1985-01-01"
RETRY_AFTER = timedelta(hours=1)

# name -> what it is (FRED's own series ids)
SERIES = {
    "DFF": "Effective federal funds rate (daily, %)",
    "DGS3MO": "3-month Treasury yield (daily, %)",
    "DGS2": "2-year Treasury yield (daily, %)",
    "DGS10": "10-year Treasury yield (daily, %)",
    "T10Y2Y": "10-year minus 2-year yield (daily, % points)",
    "BAA10Y": "Moody's Baa corporate yield minus the 10-year (daily, % points): the credit spread",
    "VIXCLS": "Cboe VIX close (daily)",
    "UNRATE": "Unemployment rate (monthly, %)",
    "CPIAUCSL": "Consumer price index (monthly)",
    "USREC": "NBER recession indicator (monthly, 1 in a recession)",
}


# Days after its date that a value was known. Market series are known that day; the
# monthly ones are published weeks later, and NBER names a recession a year or more after.
# (Revisions are ignored: the values are today's.)
LAG_DAYS = {"UNRATE": 35, "CPIAUCSL": 45, "USREC": 365}


def fred_key(env_file: Path = ENV_FILE) -> str | None:
    return _env_value(KEY_NAME, env_file)


def parse_observations(body: object) -> list[tuple[date, float]]:
    """(day, value) pairs, oldest first; FRED's "." (no value that day) is left out."""
    if not isinstance(body, dict) or not isinstance(body.get("observations"), list):
        raise FetchError("FRED's answer had no observations")
    out = []
    for row in body["observations"]:
        try:
            out.append((date.fromisoformat(row["date"]), float(row["value"])))
        except (KeyError, ValueError, TypeError):
            continue  # "." or a malformed row
    return sorted(out)


def fetch_series(series: str, key: str, since: date | None = None) -> list[tuple[date, float]]:
    """One series from `since` (default: all of it); safe to run in a worker thread."""
    start = since.isoformat() if since else FIRST_DAY
    query = urllib.parse.urlencode({"series_id": series, "api_key": key, "file_type": "json", "observation_start": start})
    body, _ = request_json(f"{URL}?{query}", {}, timeout=30, service="FRED")
    return parse_observations(body)


class Macro:
    def __init__(
        self,
        store: Store,
        key: str | None,
        fetch: Callable[[str, str], list[tuple[date, float]]] | None = None,
        fetch_since: Callable[[str, str, date], list[tuple[date, float]]] | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.store = store
        self.key = key
        self._fetch = fetch
        self._fetch_since = fetch_since
        self._now = now
        self._db: sqlite3.Connection = store.connection
        self._db.execute("CREATE TABLE IF NOT EXISTS macro (series TEXT NOT NULL, day TEXT NOT NULL, value REAL NOT NULL, PRIMARY KEY (series, day))")

    @property
    def configured(self) -> bool:
        return bool(self.key)

    @property
    def fetched(self) -> dict[str, datetime]:
        return {k: datetime.fromisoformat(v) for k, v in self.store.get("macro_fetched", {}).items()}

    def due(self) -> list[str]:
        """Series never fetched (nothing, a while after a failure). Later days come
        only when asked for (`fetch_new`)."""
        retry = self.store.get("macro_retry")
        now = self._now()
        if retry and now < datetime.fromisoformat(retry):
            return []
        have = self.fetched
        return [s for s in SERIES if s not in have]

    def fetch(self, series: str) -> list[tuple[date, float]]:
        """One request (safe in a worker thread: it doesn't touch the store)."""
        assert self.key
        return (self._fetch or fetch_series)(series, self.key)

    def last_day(self, series: str) -> date | None:
        row = self._db.execute("SELECT MAX(day) FROM macro WHERE series = ?", (series,)).fetchone()
        return date.fromisoformat(row[0]) if row and row[0] else None

    def fetch_new(self, series: str, last: date) -> list[tuple[date, float]]:
        """One request for the days since `last`, the series' last saved value
        (`last_day`; included); safe in a worker thread (it doesn't touch the store)."""
        assert self.key
        return (self._fetch_since or fetch_series)(series, self.key, last)

    def record(self, series: str, rows: list[tuple[date, float]]) -> None:
        with self._db:
            self._db.executemany("INSERT OR REPLACE INTO macro (series, day, value) VALUES (?, ?, ?)", [(series, d.isoformat(), v) for d, v in rows])
        fetched = self.store.get("macro_fetched", {})
        fetched[series] = self._now().isoformat()
        self.store.set("macro_fetched", fetched)
        self.store.set("macro_error", None)

    def note(self, message: str) -> None:
        """A problem with one series: shown in the status; the others carry on."""
        self.store.set("macro_error", {"message": message, "at": self._now().isoformat()})

    def failed(self, message: str) -> None:
        self.store.set("macro_error", {"message": message, "at": self._now().isoformat()})
        self.store.set("macro_retry", (self._now() + RETRY_AFTER).isoformat())

    def series(self, name: str, start: date | None = None, end: date | None = None) -> list[tuple[date, float]]:
        rows = self._db.execute(
            "SELECT day, value FROM macro WHERE series = ? AND day >= ? AND day <= ? ORDER BY day",
            (name, (start or date.min).isoformat(), (end or date.max).isoformat()),
        ).fetchall()
        return [(date.fromisoformat(d), v) for d, v in rows]

    def latest(self, name: str, day: date) -> tuple[date, float] | None:
        """The last value known on `day` (published by then), with its date."""
        known = day - timedelta(days=LAG_DAYS.get(name, 0))
        row = self._db.execute("SELECT day, value FROM macro WHERE series = ? AND day <= ? ORDER BY day DESC LIMIT 1", (name, known.isoformat())).fetchone()
        return (date.fromisoformat(row[0]), row[1]) if row else None

    def status(self) -> dict:
        error = self.store.get("macro_error")
        return {
            "configured": self.configured,
            "available": len(self.fetched),
            "total": len(SERIES),
            "last_error": error["message"] if error else None,
        }
