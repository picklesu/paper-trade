"""Real option data, once a day, for calibrating option prices (Phase 7.5).

For each symbol: its whole option chain from Alpaca's free indicative feed
(paged, 1,000 contracts a request) and each contract's open interest (from the
trading API's contract list, also free), plus a year of daily bars (realized
volatility, the shares traded) and, with an Alpha Vantage key, the next real
earnings dates. `calibration.calibrate` turns each chain into a small summary,
saved per symbol as soon as it's done, so an interrupted sync carries on where
it stopped. Requests share Alpaca's 200 a minute with the stock prices: they
slow down when the rate-limit headers say few are left.

The Alpha Vantage key comes from ALPHAVANTAGE_API_KEY (environment or
`backend/.env`); without it, the shape fits can't leave out the expirations
around earnings, and no implied earnings move is measured.
"""

import csv
import http.client
import io
import json
import logging
import math
import threading
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np

from paper_trade.market.calendar import (
    MARKET_TZ,
    is_trading_day,
    previous_trading_day,
    session_close,
    session_open,
)
from paper_trade.market.calibration import Earnings, calibrate
from paper_trade.realprices import (
    ENV_FILE,
    Credentials,
    FetchError,
    _env_value,
    auth_headers,
    request_json,
    tls_context,
)
from paper_trade.store import Store

OPTIONS_URL = "https://data.alpaca.markets/v1beta1/options/snapshots/"
CONTRACTS_URL = "https://paper-api.alpaca.markets/v2/options/contracts"
BARS_URL = "https://data.alpaca.markets/v2/stocks/bars"
EARNINGS_URL = "https://www.alphavantage.co/query"
AV_KEY_NAME = "ALPHAVANTAGE_API_KEY"
REFRESH_AFTER = timedelta(hours=20)  # a symbol's chain is fetched again after this
BAR_DAYS = 400  # calendar days of daily bars (a year of trading days and some)
RATE_RESERVE = 20  # requests left in Alpaca's minute below which syncing waits
LIVE_AFTER = timedelta(hours=1)  # into the session, before today's option volume is used
SIP_DELAY = timedelta(minutes=20)  # the free plan's consolidated bars end 15 minutes ago

log = logging.getLogger(__name__)


def av_key(env_file: Path = ENV_FILE) -> str | None:
    return _env_value(AV_KEY_NAME, env_file)


class RateLimit:
    """Alpaca's requests-per-minute, from its response headers, shared by threads."""

    def __init__(self, reserve: int = RATE_RESERVE, sleep: Callable[[float], None] = time.sleep) -> None:
        self.reserve = reserve
        self._sleep = sleep
        self._lock = threading.Lock()
        self._remaining: int | None = None
        self._reset: float = 0

    def wait(self) -> None:
        with self._lock:
            if self._remaining is not None and self._remaining <= self.reserve:
                pause = self._reset - time.time()
                if pause > 0:
                    self._sleep(min(pause + 0.5, 60))
                self._remaining = None

    def update(self, headers: dict[str, str]) -> None:
        with self._lock:
            lower = {k.lower(): v for k, v in headers.items()}  # header names ignore case
            try:
                self._remaining = int(lower["x-ratelimit-remaining"])
                self._reset = float(lower["x-ratelimit-reset"])
            except (KeyError, ValueError):
                pass


LIMIT = RateLimit()  # market data
TRADING_LIMIT = RateLimit()  # the trading API (contracts) counts separately
AVERAGE_DAYS = 20  # sessions averaged for the stock's typical volume


def fetch_chain(symbol: str, creds: Credentials, limit: RateLimit = LIMIT) -> dict[str, dict]:
    """Every contract's snapshot (OCC symbol -> snapshot) for one underlying."""
    snapshots: dict[str, dict] = {}
    token = None
    while True:
        query = {"feed": "indicative", "limit": 1000} | ({"page_token": token} if token else {})
        limit.wait()
        body, headers = request_json(f"{OPTIONS_URL}{symbol}?{urllib.parse.urlencode(query)}", auth_headers(creds), 30)
        limit.update(headers)
        if not isinstance(body, dict):
            raise FetchError(f"unexpected response for {symbol}'s options")
        snapshots.update(body.get("snapshots") or {})
        if not (token := body.get("next_page_token")):
            return snapshots


def fetch_open_interest(symbol: str, creds: Credentials, today: date, limit: RateLimit = TRADING_LIMIT) -> dict[str, int]:
    """Each listed contract's open interest (as of the last report, usually the
    session before last), from the trading API's contract list."""
    out: dict[str, int] = {}
    token = None
    last = (today + timedelta(days=366 * 4)).isoformat()  # without it, only this week's are listed
    while True:
        query = {"underlying_symbols": symbol, "status": "active", "expiration_date_lte": last, "limit": 10000}
        query |= {"page_token": token} if token else {}
        limit.wait()
        body, headers = request_json(f"{CONTRACTS_URL}?{urllib.parse.urlencode(query)}", auth_headers(creds), 30)
        limit.update(headers)
        if not isinstance(body, dict):
            raise FetchError(f"unexpected response for {symbol}'s contracts")
        for contract in body.get("option_contracts") or []:
            if contract.get("symbol") and contract.get("open_interest"):
                out[contract["symbol"]] = int(float(contract["open_interest"]))
        if not (token := body.get("next_page_token")):
            return out


@dataclass(frozen=True)
class Bars:
    days: list[date]
    closes: np.ndarray
    volumes: np.ndarray


def fetch_bars(symbols: list[str], creds: Credentials, now: datetime, limit: RateLimit = LIMIT) -> dict[str, Bars]:
    """A year of daily bars (split- and dividend-adjusted, all exchanges) for each symbol."""
    start = (now - timedelta(days=BAR_DAYS)).date().isoformat()
    end = (now - SIP_DELAY).astimezone(UTC).isoformat().replace("+00:00", "Z")
    rows: dict[str, list] = {}
    token = None
    while True:
        query = {
            "symbols": ",".join(symbols),
            "timeframe": "1Day",
            "start": start,
            "end": end,
            "limit": 10000,
            "adjustment": "all",
            "feed": "sip",
        } | ({"page_token": token} if token else {})
        limit.wait()
        body, headers = request_json(f"{BARS_URL}?{urllib.parse.urlencode(query)}", auth_headers(creds), 30)
        limit.update(headers)
        if not isinstance(body, dict):
            raise FetchError("unexpected response for daily bars")
        for symbol, bars in (body.get("bars") or {}).items():
            rows.setdefault(symbol, []).extend(bars)
        if not (token := body.get("next_page_token")):
            break
    out = {}
    for symbol, bars in rows.items():
        bars = sorted(bars, key=lambda b: b["t"])
        try:
            out[symbol] = Bars(
                [datetime.fromisoformat(b["t"]).astimezone(MARKET_TZ).date() for b in bars],
                np.array([float(b["c"]) for b in bars]),
                np.array([float(b.get("v") or 0) for b in bars]),
            )
        except (KeyError, TypeError, ValueError):
            raise FetchError(f"unreadable daily bars for {symbol}") from None
    return out


def parse_earnings(text: str, symbols: list[str], today: date) -> dict[str, Earnings]:
    """Each symbol's next report from Alpha Vantage's EARNINGS_CALENDAR CSV."""
    wanted, out = set(symbols), {}
    for row in csv.DictReader(io.StringIO(text)):
        symbol = row.get("symbol", "")
        try:
            day = date.fromisoformat(row.get("reportDate", ""))
        except ValueError:
            continue
        if symbol in wanted and day >= today and (symbol not in out or day < out[symbol].day):
            out[symbol] = Earnings(day, (row.get("timeOfTheDay") or "").strip())
    return out


def fetch_earnings(key: str, symbols: list[str], today: date) -> dict[str, Earnings]:
    """Upcoming reports (one request for all symbols; the reply is CSV, not JSON)."""
    query = urllib.parse.urlencode({"function": "EARNINGS_CALENDAR", "horizon": "3month", "apikey": key})
    try:
        with urllib.request.urlopen(f"{EARNINGS_URL}?{query}", timeout=30, context=tls_context()) as response:
            text = response.read().decode()
    except (OSError, http.client.HTTPException, UnicodeDecodeError) as e:
        raise FetchError(f"couldn't read Alpha Vantage's answer ({getattr(e, 'reason', e)})") from None
    if not text.startswith("symbol,"):  # a JSON note: a bad key or the daily limit
        raise FetchError(f"Alpha Vantage didn't send the earnings calendar: {text[:160]}")
    return parse_earnings(text, symbols, today)


def parse_treasury_rate(body: str) -> tuple[date, float]:
    """The latest daily 3-month Treasury yield from Alpha Vantage's JSON, as a
    continuously compounded rate (the yield is a bond-equivalent, semiannual one)."""
    try:
        rows = json.loads(body)["data"]
    except (ValueError, KeyError, TypeError):
        raise FetchError(f"Alpha Vantage didn't send the Treasury yield: {body[:160]}") from None
    for row in rows:  # newest first; holidays have "." for a value
        try:
            percent = float(row["value"])
        except (KeyError, TypeError, ValueError):
            continue
        return date.fromisoformat(row["date"]), round(2 * math.log1p(percent / 200), 6)
    raise FetchError("Alpha Vantage's Treasury yield had no values")


def fetch_treasury_rate(key: str) -> tuple[date, float]:
    query = urllib.parse.urlencode({"function": "TREASURY_YIELD", "interval": "daily", "maturity": "3month", "apikey": key})
    try:
        with urllib.request.urlopen(f"{EARNINGS_URL}?{query}", timeout=30, context=tls_context()) as response:
            body = response.read().decode()
    except (OSError, http.client.HTTPException, UnicodeDecodeError) as e:
        raise FetchError(f"couldn't read Alpha Vantage's answer ({getattr(e, 'reason', e)})") from None
    return parse_treasury_rate(body)


@dataclass(frozen=True)
class Context:
    """What every symbol's calibration in one sync shares."""

    fetched: datetime
    bars: dict[str, Bars]
    earnings: dict[str, Earnings] | None  # None: no calendar (no key, or it failed)


def complete_session(now: datetime) -> date:
    """The latest session that has closed by `now`."""
    today = now.astimezone(MARKET_TZ).date()
    if is_trading_day(today) and now >= session_close(today):
        return today
    return previous_trading_day(today)


def live_session(now: datetime) -> date | None:
    """Today, if its session has run long enough (LIVE_AFTER) to measure the mix of
    option volume, and hasn't closed."""
    today = now.astimezone(MARKET_TZ).date()
    if is_trading_day(today) and session_open(today) + LIVE_AFTER <= now < session_close(today):
        return today
    return None


def summarize(symbol: str, creds: Credentials, ctx: Context, now: datetime) -> dict:
    """Fetch one symbol's chain and calibrate (safe in a worker thread)."""
    snapshots = fetch_chain(symbol, creds)
    try:
        open_interest = fetch_open_interest(symbol, creds, now.astimezone(MARKET_TZ).date())
    except FetchError as e:  # optional: open interest is then made up
        log.warning("no open interest for %s: %s", symbol, e)
        open_interest = None
    day = complete_session(now)
    bars = ctx.bars.get(symbol)
    closes = volume = average = None
    if bars is not None:
        through = [i for i, d in enumerate(bars.days) if d <= day]
        if through:
            closes = bars.closes[: through[-1] + 1]
            volume = float(bars.volumes[through[-1]]) if bars.days[through[-1]] == day else None
            average = float(np.mean(bars.volumes[: through[-1] + 1][-AVERAGE_DAYS:]))
    earnings = ctx.earnings.get(symbol) if ctx.earnings else None
    return calibrate(
        symbol,
        snapshots,
        now,
        closes,
        day,
        volume,
        live_session(now),
        earnings,
        open_interest,
        average,
    )


class OptionData:
    """Saved calibrations (per symbol) and how the last sync went, in the store."""

    def __init__(
        self,
        store: Store,
        creds: Credentials | None,
        earnings_key: str | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.store = store
        self.creds = creds
        self.earnings_key = earnings_key
        self._now = now

    @property
    def configured(self) -> bool:
        return self.creds is not None

    @property
    def saved(self) -> dict[str, dict]:
        return self.store.get("option_calibration", {})

    @property
    def rate(self) -> float | None:
        """The saved risk-free rate, if one was ever fetched."""
        return self.store.get("risk_free_rate", {}).get("rate")

    def rate_due(self) -> bool:
        saved = self.store.get("risk_free_rate", {})
        return bool(self.earnings_key) and (
            not saved or self._now() - datetime.fromisoformat(saved["fetched"]) >= REFRESH_AFTER
        )

    def record_rate(self, day: date, rate: float) -> None:
        self.store.set("risk_free_rate", {"rate": rate, "as_of": day.isoformat(), "fetched": self._now().isoformat()})

    def _sync_state(self) -> dict:
        return self.store.get("option_sync", {})

    def due(self, symbols: list[str]) -> list[str]:
        """Symbols whose chain is missing or older than REFRESH_AFTER."""
        saved, now = self.saved, self._now()
        return [s for s in symbols if s not in saved or now - datetime.fromisoformat(saved[s]["fetched"]) >= REFRESH_AFTER]

    def attempt(self) -> None:
        """A sync begins: clear the last problem (and the old attempt/retry fields)."""
        self.store.set("option_sync", {"error": None})

    def record(self, symbol: str, summary: dict) -> None:
        saved = self.saved
        saved[symbol] = summary
        self.store.set("option_calibration", saved)

    def note(self, message: str) -> None:
        """Something to show the user that didn't stop the sync."""
        state = self._sync_state()
        self.store.set("option_sync", state | {"error": message})

    def failed(self, message: str) -> None:
        """The sync stopped: show why."""
        self.note(message)

    def status(self, symbols: list[str]) -> dict:
        saved = self.saved
        days = [saved[s]["as_of"] for s in symbols if s in saved and saved[s].get("shape")]
        fetched = [saved[s]["fetched"] for s in symbols if s in saved]
        state = self._sync_state()
        return {
            "configured": self.configured,
            "earnings": self.earnings_key is not None,
            "calibrated": len(days),
            "total": len(symbols),
            "oldest": min(days) if days else None,
            "newest": max(days) if days else None,
            "fetched": max(fetched) if fetched else None,
            "last_error": state.get("error"),
        }
