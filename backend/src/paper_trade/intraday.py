"""Real one-minute bars, kept only to fit the intraday volume profile.

Alpaca's consolidated (SIP) minute bars, from 2016. The app never reads these: they feed
`scripts/fit_intraday_volume.py`, which saves a small profile in the repo. Fetched on demand
by `./server.sh update intraday`.

A day is stored as 391 slots: minute 0 is the 9:30 bar (it holds the opening cross) and
minute 390 is the 16:00 bar, which holds the closing cross. Pre- and after-market bars (16:01 on)
are dropped. A slot with no trades has volume 0 and close 0. Closes are float32 (about 7 digits): they\nare for relative moves, not prices.
"""

import struct
import urllib.parse
from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, date, datetime, time

from paper_trade.market import symbols as market_symbols
from paper_trade.market.calendar import MARKET_TZ
from paper_trade.optiondata import BARS_URL, LIMIT, SIP_DELAY, RateLimit
from paper_trade.realprices import Credentials, FetchError, auth_headers, request_json
from paper_trade.store import Store

SLOTS = 391  # 9:30 through 16:00, one-minute bars
OPEN_MINUTE = 9 * 60 + 30
FIRST_DAY = date(2016, 1, 4)  # Alpaca's free history starts around here
# Every symbol: closing and opening shares differ a lot between stocks (7% to 30% of the day
# in the last five minutes), so each gets its own profile.
SYMBOLS = tuple(spec.symbol for spec in market_symbols.SYMBOLS)

Fetch = Callable[[str, date, datetime, Credentials, datetime], dict[date, tuple[list[int], list[float]]]]


def slot_of(timestamp: str) -> tuple[date, int] | None:
    """The New York day and slot of a bar's start time, or None outside 9:30-16:00."""
    t = datetime.fromisoformat(timestamp).astimezone(MARKET_TZ)
    slot = t.hour * 60 + t.minute - OPEN_MINUTE
    return (t.date(), slot) if 0 <= slot < SLOTS else None


def parse_minutes(bars: list[dict]) -> dict[date, tuple[list[int], list[float]]]:
    """Group Alpaca's bars into days of (volumes, closes) by slot."""
    days: dict[date, tuple[list[int], list[float]]] = {}
    for bar in bars:
        placed = slot_of(bar["t"])
        if placed is None:
            continue
        day, slot = placed
        volumes, closes = days.setdefault(day, ([0] * SLOTS, [0.0] * SLOTS))
        volumes[slot] = int(bar.get("v") or 0)
        closes[slot] = float(bar.get("c") or 0.0)
    return days


def fetch_minutes(
    symbol: str, since: date, now: datetime, creds: Credentials, limit: RateLimit = LIMIT, until: datetime | None = None
) -> dict[date, tuple[list[int], list[float]]]:
    """Every regular-session minute of `symbol` from `since` until `until`, which is at most the
    SIP delay before `now`. Days with under a quarter of the minutes traded (early closes have
    about half; glitches fewer) are dropped."""
    end = min(until or now, now - SIP_DELAY).astimezone(UTC).isoformat().replace("+00:00", "Z")
    rows: list[dict] = []
    token = None
    while True:
        query = {
            "symbols": symbol,
            "timeframe": "1Min",
            "start": since.isoformat(),
            "end": end,
            "limit": 10000,
            "adjustment": "raw",
            "feed": "sip",
        } | ({"page_token": token} if token else {})
        limit.wait()
        body, headers = request_json(f"{BARS_URL}?{urllib.parse.urlencode(query)}", auth_headers(creds), 30)
        limit.update(headers)
        if not isinstance(body, dict):
            raise FetchError("unexpected response for minute bars")
        rows.extend((body.get("bars") or {}).get(symbol, []))
        if not (token := body.get("next_page_token")):
            break
    days = parse_minutes(rows)
    return {d: v for d, v in days.items() if sum(1 for x in v[0] if x) >= SLOTS // 4}


class Intraday:
    """Saved minute bars: a row per symbol and day, volumes and closes packed as arrays."""

    def __init__(self, store: Store, creds: Credentials | None, fetch: Fetch = fetch_minutes) -> None:
        self.store = store
        self.creds = creds
        self._fetch = fetch
        self._db = store.connection
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS intraday (symbol TEXT NOT NULL, day TEXT NOT NULL, volume BLOB NOT NULL, "
            "close BLOB NOT NULL, PRIMARY KEY (symbol, day))"
        )

    @property
    def configured(self) -> bool:
        return self.creds is not None

    def last_day(self, symbol: str) -> date | None:
        row = self._db.execute("SELECT MAX(day) FROM intraday WHERE symbol = ?", (symbol,)).fetchone()
        return date.fromisoformat(row[0]) if row and row[0] else None

    def update(self, symbol: str, now: datetime) -> int:
        """Fetch the days from the last saved one (again: it may have been saved before its
        close) a year at a time, saving each year before asking for the next, so a rate limit
        or a dropped connection keeps what came before. Returns how many days were saved."""
        if self.creds is None:
            raise FetchError("no Alpaca keys: add APCA_API_KEY_ID and APCA_API_SECRET_KEY to backend/.env")
        start = self.last_day(symbol) or FIRST_DAY
        local = now.astimezone(MARKET_TZ)
        saved = 0
        while start <= local.date():
            until = datetime(start.year + 1, 1, 1, tzinfo=UTC)
            days = self._fetch(symbol, start, now, self.creds, until)
            # today's session counts once its closing bar is in (the feed runs 20 minutes behind)
            done = {d: v for d, v in days.items() if d < local.date() or local.time() >= time(16, 30)}
            self._save(symbol, done)
            saved += len(done)
            start = until.date()
        return saved

    def _save(self, symbol: str, days: dict[date, tuple[list[int], list[float]]]) -> None:
        top = 2**32 - 1  # a stored volume is an unsigned 32-bit count
        with self._transaction():
            for day, (volumes, closes) in sorted(days.items()):
                volumes = [min(max(v, 0), top) for v in volumes]
                self._db.execute(
                    "INSERT OR REPLACE INTO intraday (symbol, day, volume, close) VALUES (?, ?, ?, ?)",
                    (symbol, day.isoformat(), struct.pack(f"<{SLOTS}I", *volumes), struct.pack(f"<{SLOTS}f", *closes)),
                )

    @contextmanager
    def _transaction(self):
        self._db.execute("BEGIN")
        try:
            yield
        except BaseException:
            self._db.execute("ROLLBACK")
            raise
        self._db.execute("COMMIT")

    def days(self, symbol: str) -> list[tuple[date, list[int], list[float]]]:
        rows = self._db.execute("SELECT day, volume, close FROM intraday WHERE symbol = ? ORDER BY day", (symbol,)).fetchall()
        return [
            (date.fromisoformat(d), list(struct.unpack(f"<{SLOTS}I", v)), list(struct.unpack(f"<{SLOTS}f", c)))
            for d, v, c in rows
        ]

