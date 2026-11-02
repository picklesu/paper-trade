"""Real one-minute bars, kept only to fit the intraday volume profile.

Alpaca's consolidated (SIP) minute bars, from 2016. The app never reads these: they feed
`scripts/fit_intraday_volume.py`, which saves a small profile in the repo. Fetched on demand
by `./server.sh update intraday`.

A day is stored as 391 slots: minute 0 is the 9:30 bar (it holds the opening cross) and
minute 390 is the 16:00 bar, which holds the closing cross and the prints just after it.
Pre- and after-market bars are dropped. A slot with no trades has volume 0 and close 0.
"""

import struct
import urllib.parse
from collections.abc import Callable
from datetime import UTC, date, datetime, time

from paper_trade.market.calendar import MARKET_TZ
from paper_trade.optiondata import BARS_URL, LIMIT, SIP_DELAY, RateLimit
from paper_trade.realprices import Credentials, FetchError, auth_headers, request_json
from paper_trade.store import Store

SLOTS = 391  # 9:30 through 16:00, one-minute bars
OPEN_MINUTE = 9 * 60 + 30
FIRST_DAY = date(2016, 1, 4)  # Alpaca's free history starts around here
# One fund per kind, big and small stocks: enough to fit profiles by tier.
SYMBOLS = ("SPY", "QQQ", "IWM", "AAPL", "NVDA", "JPM", "KO", "GS")

Fetch = Callable[[str, date, datetime, Credentials], dict[date, tuple[list[int], list[float]]]]


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
    symbol: str, since: date, now: datetime, creds: Credentials, limit: RateLimit = LIMIT
) -> dict[date, tuple[list[int], list[float]]]:
    """Every regular-session minute of `symbol` from `since` until the SIP delay before `now`.
    Days with fewer than half the minutes (early closes are kept; thin glitches are not) are dropped."""
    end = (now - SIP_DELAY).astimezone(UTC).isoformat().replace("+00:00", "Z")
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
    return {d: v for d, v in days.items() if sum(1 for x in v[0] if x) >= SLOTS // 2}


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
        """Fetch the days after the last saved one (that day again: it may have been saved
        before its close) and save them; returns how many days were saved."""
        if self.creds is None:
            raise FetchError("no Alpaca keys: add APCA_API_KEY_ID and APCA_API_SECRET_KEY to backend/.env")
        last = self.last_day(symbol)
        days = self._fetch(symbol, last or FIRST_DAY, now, self.creds)
        # today's session is not finished: keep only finished days
        cutoff = (now - SIP_DELAY).astimezone(MARKET_TZ)
        done = {d: v for d, v in days.items() if d < cutoff.date() or (d == cutoff.date() and cutoff.time() > time(16, 2))}
        for day, (volumes, closes) in sorted(done.items()):
            self._db.execute(
                "INSERT OR REPLACE INTO intraday (symbol, day, volume, close) VALUES (?, ?, ?, ?)",
                (symbol, day.isoformat(), struct.pack(f"<{SLOTS}I", *volumes), struct.pack(f"<{SLOTS}f", *closes)),
            )
        return len(done)

    def days(self, symbol: str) -> list[tuple[date, list[int], list[float]]]:
        rows = self._db.execute("SELECT day, volume, close FROM intraday WHERE symbol = ? ORDER BY day", (symbol,)).fetchall()
        return [
            (date.fromisoformat(d), list(struct.unpack(f"<{SLOTS}I", v)), list(struct.unpack(f"<{SLOTS}f", c)))
            for d, v, c in rows
        ]

