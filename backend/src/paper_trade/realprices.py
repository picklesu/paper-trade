"""Real prices from Alpaca's market data API, so the simulated market can start from them.

One request returns a snapshot of every symbol. On the free (Basic) plan,
real-time prices come from the IEX exchange alone; they trade within cents of
the consolidated price, which is plenty for starting a simulation. A background
refresher fetches at startup and every 15 minutes (a minute after a failed
fetch), and opening the Sync dialog fetches again if the last fetch is more than
a minute old. "Sync to real prices" and new markets
use whatever is saved.

The keys come from the APCA_API_KEY_ID and APCA_API_SECRET_KEY environment
variables (Alpaca's own names) or from `backend/.env` (git-ignored; never commit
it: the repository is public).
"""

import functools
import http.client
import json
import logging
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import truststore

from paper_trade.market.calendar import MARKET_TZ
from paper_trade.store import Store

URL = "https://data.alpaca.markets/v2/stocks/snapshots"
KEY_ID_NAME = "APCA_API_KEY_ID"
SECRET_NAME = "APCA_API_SECRET_KEY"
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
REFRESH_EVERY = timedelta(minutes=15)  # the background refresher's interval
RETRY_AFTER = timedelta(minutes=1)  # after a failed fetch (offline at startup, a refusal)
STATUS_REFRESH_AFTER = timedelta(minutes=1)  # opening the Sync dialog fetches if older

log = logging.getLogger(__name__)


@functools.cache
def tls_context() -> ssl.SSLContext:
    """HTTPS that checks certificates against the operating system's trust store
    (the macOS Keychain): Python builds such as uv's may have no certificates of
    their own. Made on first use, so a problem here can't stop the server."""
    try:
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:
        log.warning("truststore unavailable; using Python's own certificates", exc_info=True)
        return ssl.create_default_context()


@dataclass(frozen=True)
class Credentials:
    key_id: str
    secret: str


@dataclass(frozen=True)
class RealPrice:
    price: float
    as_of: date  # the trading day the price is from
    fetched: datetime


class FetchError(Exception):
    """The service refused or sent something unusable; the message is for the user."""


def _env_value(name: str, env_file: Path) -> str | None:
    if value := os.environ.get(name, "").strip():
        return value
    if env_file.is_file():
        for line in env_file.read_text().splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() == name and value.strip():
                return value.strip().strip("\"'")
    return None


def credentials(env_file: Path = ENV_FILE) -> Credentials | None:
    key_id, secret = _env_value(KEY_ID_NAME, env_file), _env_value(SECRET_NAME, env_file)
    return Credentials(key_id, secret) if key_id and secret else None


def _trading_day(timestamp: str) -> date:
    # RFC 3339 with up to nanoseconds; fromisoformat keeps the first six digits.
    return datetime.fromisoformat(timestamp).astimezone(MARKET_TZ).date()


def parse_snapshots(body: dict, symbols: list[str]) -> dict[str, tuple[float, date]]:
    """Each symbol's latest trade price and trading day (the day's close if there's
    no trade); symbols the response leaves out are left out."""
    prices = {}
    for symbol in symbols:
        snap = body.get(symbol) or {}
        for field, key in (("latestTrade", "p"), ("dailyBar", "c")):
            if (item := snap.get(field)) and item.get(key) and item.get("t"):
                prices[symbol] = (float(item[key]), _trading_day(item["t"]))
                break
    return prices


def _refusal(code: int, body: bytes) -> FetchError:
    # Errors usually carry JSON {"message": ...}; some are an HTML page, which isn't shown.
    try:
        message = str(json.loads(body).get("message") or "")
    except (ValueError, AttributeError):
        message = ""
    detail = f"{code} {message}".strip()
    if code in (401, 403):
        return FetchError(f"Alpaca rejected the API keys ({detail}). Check {KEY_ID_NAME} and {SECRET_NAME}.")
    if code == 429:
        return FetchError("Alpaca says too many requests; the next refresh will try again.")
    return FetchError(f"Alpaca answered {detail}")


def fetch_snapshots(symbols: list[str], creds: Credentials, timeout: float = 10) -> dict[str, tuple[float, date]]:
    query = urllib.parse.urlencode({"symbols": ",".join(symbols), "feed": "iex"})
    request = urllib.request.Request(
        f"{URL}?{query}",
        headers={"APCA-API-KEY-ID": creds.key_id, "APCA-API-SECRET-KEY": creds.secret, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=tls_context()) as response:
            body = json.load(response)
    except urllib.error.HTTPError as e:
        raise _refusal(e.code, e.read()) from None
    except (OSError, http.client.HTTPException) as e:  # URLError, timeouts, dropped connections
        reason = getattr(e, "reason", None) or e
        if isinstance(reason, ssl.SSLCertVerificationError):
            raise FetchError(
                f"this computer doesn't trust the certificate Alpaca's connection presented ({reason.verify_message}); "
                "a proxy or missing certificates?"
            ) from None
        raise FetchError(f"couldn't reach Alpaca ({reason})") from None
    except ValueError:  # not JSON: e.g. a proxy's or captive portal's page
        raise FetchError("Alpaca's answer wasn't readable (not JSON); is something on the network in the way?") from None
    if not isinstance(body, dict):
        raise FetchError(f"unexpected response: {json.dumps(body)[:200]}")
    return parse_snapshots(body, symbols)


class RealPrices:
    """Saved real prices and the last fetch's outcome, in the store (saved prices
    are also kept in memory)."""

    def __init__(
        self,
        store: Store,
        creds: Credentials | None,
        fetch: Callable[[list[str], Credentials], dict[str, tuple[float, date]]] | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.store = store
        self.creds = creds
        self._fetch = fetch
        self._now = now
        self._saved: dict[str, RealPrice] | None = None
        for old in ("real_prices_usage", "real_prices_attempts", "real_prices_pause"):  # from Alpha Vantage
            store.delete(old)

    @property
    def configured(self) -> bool:
        return self.creds is not None

    @property
    def saved(self) -> dict[str, RealPrice]:
        if self._saved is None:
            self._saved = {
                s: RealPrice(v["price"], date.fromisoformat(v["as_of"]), datetime.fromisoformat(v["fetched"]))
                for s, v in self.store.get("real_prices", {}).items()
            }
        return self._saved

    def last_attempt(self) -> datetime | None:
        at = self.store.get("real_prices_attempt")
        return datetime.fromisoformat(at) if at else None

    def stale(self, age: timedelta) -> bool:
        """Whether the last fetch (successful or not) was longer than `age` ago."""
        last = self.last_attempt()
        return last is None or self._now() - last >= age

    def seconds_until_refresh(self) -> float:
        """Until the background refresher's next fetch: REFRESH_EVERY after the
        last fetch, or RETRY_AFTER if it failed."""
        last = self.last_attempt()
        if last is None:
            return 0
        wait = RETRY_AFTER if self.store.get("real_prices_retry") else REFRESH_EVERY
        return max(0.0, (last + wait - self._now()).total_seconds())

    def fetch(self, symbols: list[str]) -> dict[str, tuple[float, date]]:
        """One request (safe to run in a worker thread: it doesn't touch the store)."""
        assert self.creds
        return (self._fetch or fetch_snapshots)(symbols, self.creds)

    def attempt(self) -> None:
        """A fetch is about to be made."""
        self.store.set("real_prices_attempt", self._now().isoformat())
        self.store.set("real_prices_retry", False)

    def record(self, prices: dict[str, tuple[float, date]], symbols: list[str]) -> None:
        fetched = self._now()
        saved = self.store.get("real_prices", {})
        for symbol, (price, as_of) in prices.items():
            saved[symbol] = {"price": price, "as_of": as_of.isoformat(), "fetched": fetched.isoformat()}
            self.saved[symbol] = RealPrice(price, as_of, fetched)
        self.store.set("real_prices", saved)
        missing = [s for s in symbols if s not in prices]
        self.store.set("real_prices_error", None)
        if not prices:
            self.failed("Alpaca sent no prices")
        elif missing:
            self.failed(f"no price for {', '.join(missing)}")

    def fetch_failed(self, message: str) -> None:
        """The fetch got nothing: show why, and retry soon."""
        self.failed(message)
        self.store.set("real_prices_retry", True)

    def failed(self, message: str) -> None:
        self.store.set("real_prices_error", {"message": message, "at": self._now().isoformat()})

    def status(self, symbols: list[str]) -> dict:
        saved = self.saved
        days = [saved[s].as_of for s in symbols if s in saved]
        fetched = [saved[s].fetched for s in symbols if s in saved]
        error = self.store.get("real_prices_error")
        return {
            "configured": self.configured,
            "available": sum(s in saved for s in symbols),
            "total": len(symbols),
            "oldest": min(days).isoformat() if days else None,
            "newest": max(days).isoformat() if days else None,
            "fetched": max(fetched).isoformat() if fetched else None,
            "last_error": error["message"] if error else None,
            "refresh_minutes": round(REFRESH_EVERY.total_seconds() / 60),
        }
