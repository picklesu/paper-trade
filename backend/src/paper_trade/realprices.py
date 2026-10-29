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

log = logging.getLogger(__name__)


class RateLimited(Exception):
    """The service refused for now (the daily or per-minute limit)."""


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
    if "Information" in body or "Note" in body:
        raise RateLimited(body.get("Information") or body.get("Note"))
    raise ValueError(f"unexpected response: {json.dumps(body)[:200]}")


def fetch_quote(symbol: str, key: str, timeout: float = 15) -> tuple[float, date]:
    query = urllib.parse.urlencode({"function": "GLOBAL_QUOTE", "symbol": symbol, "apikey": key})
    with urllib.request.urlopen(f"{URL}?{query}", timeout=timeout) as response:
        return parse_quote(json.load(response))


class RealPrices:
    """Saved real prices and the daily request budget, in the store."""

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

    @property
    def saved(self) -> dict[str, RealPrice]:
        return {
            s: RealPrice(v["price"], date.fromisoformat(v["as_of"]), datetime.fromisoformat(v["fetched"]))
            for s, v in self.store.get("real_prices", {}).items()
        }

    def requests_left(self) -> int:
        usage = self.store.get("real_prices_usage", {})
        return DAILY_REQUESTS - usage.get("count", 0) if usage.get("day") == self._today() else DAILY_REQUESTS

    def due(self, symbols: list[str]) -> list[str]:
        """Symbols worth refreshing, never-fetched first, then the oldest."""
        saved, now = self.saved, self._now()
        stale = [s for s in symbols if s not in saved or now - saved[s].fetched >= FRESH_FOR]
        return sorted(stale, key=lambda s: (s in saved, saved[s].fetched if s in saved else now))

    def next_due(self, symbols: list[str]) -> str | None:
        """The symbol to fetch next, or None (no key, nothing due, or no requests left today)."""
        due = self.due(symbols)
        if not self.key or not due or self.requests_left() <= 0:
            return None
        return due[0]

    def fetch(self, symbol: str) -> tuple[float, date]:
        """One request (safe to run in a worker thread: it doesn't touch the store)."""
        assert self.key
        return (self._fetch or fetch_quote)(symbol, self.key)

    def count_request(self) -> None:
        self._set_used(DAILY_REQUESTS - self.requests_left() + 1)

    def exhaust(self) -> None:
        """The service refused: give up the rest of today's requests."""
        self._set_used(DAILY_REQUESTS)

    def record(self, symbol: str, price: float, as_of: date) -> None:
        prices = self.store.get("real_prices", {})
        prices[symbol] = {"price": price, "as_of": as_of.isoformat(), "fetched": self._now().isoformat()}
        self.store.set("real_prices", prices)

    def status(self, symbols: list[str]) -> dict:
        saved = self.saved
        days = [saved[s].as_of for s in symbols if s in saved]
        return {
            "configured": bool(self.key),
            "available": sum(s in saved for s in symbols),
            "total": len(symbols),
            "oldest": min(days).isoformat() if days else None,
            "newest": max(days).isoformat() if days else None,
            "requests_left": self.requests_left(),
            "daily_requests": DAILY_REQUESTS,
        }

    def _today(self) -> str:
        return self._now().astimezone(MARKET_TZ).date().isoformat()

    def _set_used(self, count: int) -> None:
        self.store.set("real_prices_usage", {"day": self._today(), "count": count})
