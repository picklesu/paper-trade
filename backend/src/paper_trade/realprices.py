"""Real prices from Alpha Vantage, so the simulated market can start from them.

The free plan allows about 25 requests a day, one symbol per request, so a
background refresher spends that budget on the symbols whose prices are oldest,
spacing requests out. Fetched prices are saved; "Sync to real prices" and new
markets use whatever is saved.

The API key comes from the ALPHAVANTAGE_API_KEY environment variable or from
`backend/.env` (git-ignored; never commit it: the repository is public).
"""

import json
import logging
import os
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from paper_trade.market.calendar import MARKET_TZ
from paper_trade.store import Store

URL = "https://www.alphavantage.co/query"
KEY_NAME = "ALPHAVANTAGE_API_KEY"
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
DAILY_REQUESTS = 25
REQUEST_SPACING_SECONDS = 15  # stays under the free plan's per-minute limit too
FRESH_FOR = timedelta(hours=12)  # a price fetched more recently isn't refreshed
RETRY_AFTER = timedelta(hours=1)  # a symbol whose fetch failed waits this long
# After a refusal: a burst limit clears within a minute; for the daily limit, check
# hourly (when the service's day starts isn't documented).
THROTTLE_PAUSE = timedelta(minutes=1)
DAILY_LIMIT_PAUSE = timedelta(hours=1)

log = logging.getLogger(__name__)


class RateLimited(Exception):
    """The service refused for now."""


class DailyLimit(RateLimited):
    """The day's requests are used up."""


class Throttled(RateLimited):
    """Too many requests in a short time."""


@dataclass(frozen=True)
class RealPrice:
    price: float
    as_of: date  # the trading day the price is from
    fetched: datetime


def api_key(env_file: Path = ENV_FILE) -> str | None:
    if key := os.environ.get(KEY_NAME):
        return key.strip()
    if env_file.is_file():
        for line in env_file.read_text().splitlines():
            name, sep, value = line.partition("=")
            if sep and name.strip() == KEY_NAME and value.strip():
                return value.strip().strip("\"'")
    return None


def parse_quote(body: dict) -> tuple[float, date]:
    """A GLOBAL_QUOTE response's price and trading day."""
    quote = body.get("Global Quote")
    if quote and "05. price" in quote:
        return float(quote["05. price"]), date.fromisoformat(quote["07. latest trading day"])
    message = str(body.get("Information") or body.get("Note") or body.get("Error Message") or "")
    text = message.lower()
    if "per day" in text or "daily" in text:
        raise DailyLimit(message)
    if "rate limit" in text or "per second" in text or "per minute" in text or "sparingly" in text:
        raise Throttled(message)
    # Anything else (e.g. an invalid key) is a problem to show, not a limit to wait out.
    raise ValueError(message or f"unexpected response: {json.dumps(body)[:200]}")


def fetch_quote(symbol: str, key: str, timeout: float = 15) -> tuple[float, date]:
    query = urllib.parse.urlencode({"function": "GLOBAL_QUOTE", "symbol": symbol, "apikey": key})
    with urllib.request.urlopen(f"{URL}?{query}", timeout=timeout) as response:
        return parse_quote(json.load(response))


class RealPrices:
    """Saved real prices, the daily request budget and the refresher's state, in
    the store (saved prices are also kept in memory)."""

    def __init__(
        self,
        store: Store,
        key: str | None,
        fetch: Callable[[str, str], tuple[float, date]] | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.store = store
        self.key = key
        self._fetch = fetch
        self._now = now
        self._saved: dict[str, RealPrice] | None = None

    @property
    def saved(self) -> dict[str, RealPrice]:
        if self._saved is None:
            self._saved = {
                s: RealPrice(v["price"], date.fromisoformat(v["as_of"]), datetime.fromisoformat(v["fetched"]))
                for s, v in self.store.get("real_prices", {}).items()
            }
        return self._saved

    def requests_left(self) -> int:
        usage = self.store.get("real_prices_usage", {})
        return DAILY_REQUESTS - usage.get("count", 0) if usage.get("day") == self._today() else DAILY_REQUESTS

    def due(self, symbols: list[str]) -> list[str]:
        """Symbols worth fetching: never fetched first, then the oldest; a symbol
        whose fetch failed waits RETRY_AFTER (so it can't take the whole budget)."""
        saved, now = self.saved, self._now()
        attempts = {s: datetime.fromisoformat(t) for s, t in self.store.get("real_prices_attempts", {}).items()}

        def last(s: str) -> datetime | None:
            times = [t for t in (saved[s].fetched if s in saved else None, attempts.get(s)) if t]
            return max(times) if times else None

        due = [
            s
            for s in symbols
            if (s not in saved or now - saved[s].fetched >= FRESH_FOR)
            and (s not in attempts or now - attempts[s] >= RETRY_AFTER)
        ]
        return sorted(due, key=lambda s: (s in saved, last(s) or now))

    def paused_until(self) -> datetime | None:
        until = self.store.get("real_prices_pause")
        return datetime.fromisoformat(until) if until else None

    def next_due(self, symbols: list[str]) -> str | None:
        """The symbol to fetch next, or None (no key, paused, nothing due, or no
        requests left today)."""
        paused = self.paused_until()
        if not self.key or (paused and self._now() < paused) or self.requests_left() <= 0:
            return None
        due = self.due(symbols)
        return due[0] if due else None

    def seconds_until_retry(self, idle: float) -> float:
        """How long the refresher should wait when nothing is due: until a pause
        ends, if that's sooner than `idle`."""
        paused = self.paused_until()
        if paused and paused > self._now():
            return min(idle, (paused - self._now()).total_seconds())
        return idle

    def fetch(self, symbol: str) -> tuple[float, date]:
        """One request (safe to run in a worker thread: it doesn't touch the store)."""
        assert self.key
        return (self._fetch or fetch_quote)(symbol, self.key)

    def attempt(self, symbol: str) -> None:
        """A request for `symbol` is about to be made: count it."""
        self.store.set("real_prices_usage", {"day": self._today(), "count": DAILY_REQUESTS - self.requests_left() + 1})
        attempts = self.store.get("real_prices_attempts", {})
        attempts[symbol] = self._now().isoformat()
        self.store.set("real_prices_attempts", attempts)

    def record(self, symbol: str, price: float, as_of: date) -> None:
        fetched = self._now()
        prices = self.store.get("real_prices", {})
        prices[symbol] = {"price": price, "as_of": as_of.isoformat(), "fetched": fetched.isoformat()}
        self.store.set("real_prices", prices)
        self.saved[symbol] = RealPrice(price, as_of, fetched)
        self.store.set("real_prices_error", None)

    def refused(self, error: RateLimited) -> None:
        """The service refused: pause (briefly for a burst limit, an hour for the daily one)."""
        pause = DAILY_LIMIT_PAUSE if isinstance(error, DailyLimit) else THROTTLE_PAUSE
        self.store.set("real_prices_pause", (self._now() + pause).isoformat())
        self.failed(str(error))

    def failed(self, message: str) -> None:
        self.store.set("real_prices_error", {"message": message, "at": self._now().isoformat()})

    def status(self, symbols: list[str]) -> dict:
        saved = self.saved
        days = [saved[s].as_of for s in symbols if s in saved]
        error = self.store.get("real_prices_error")
        return {
            "configured": bool(self.key),
            "available": sum(s in saved for s in symbols),
            "total": len(symbols),
            "oldest": min(days).isoformat() if days else None,
            "newest": max(days).isoformat() if days else None,
            "requests_left": self.requests_left(),
            "daily_requests": DAILY_REQUESTS,
            "last_error": error["message"] if error else None,
        }

    def _today(self) -> str:
        return self._now().astimezone(MARKET_TZ).date().isoformat()
