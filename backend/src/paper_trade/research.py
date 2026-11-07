"""Real data gathered to study and calibrate the simulated future (never read by the app).

Three sets, each fetched on demand by `./server.sh update events|earnings|flow` and read by
scripts in `backend/scripts`:

* `Releases`: the dates of scheduled economic releases (jobs report, CPI, FOMC...) from FRED,
  past and announced. One request per release.
* `EarningsHistory`: each company's past report dates and surprise, from Alpha Vantage (one
  request per symbol; its free plan allows 25 a day, so run it again the next day to resume).
* `Flow`: buyer- and seller-initiated volume per minute for chosen days, from Alpaca's trades
  (SIP). A trade is classified by the tick test: above the last different price a buy, below
  a sell, at it the same side as before. No quotes are fetched (they are ten times the size),
  so it is less exact than Lee-Ready (the tick test is usually right on roughly 75-80% of trades in studies; not measured here); it shows the day's
  imbalance, not who caused what.
"""

import json
import re
import struct
import time
import urllib.parse
from collections.abc import Callable
from datetime import UTC, date, datetime

from paper_trade.intraday import OPEN_MINUTE, SLOTS
from paper_trade.market.calendar import MARKET_TZ
from paper_trade.optiondata import EARNINGS_URL, LIMIT, SIP_DELAY, RateLimit
from paper_trade.realprices import Credentials, FetchError, FetchStop, auth_headers, request_json
from paper_trade.store import Store

FRED_RELEASE_URL = "https://api.stlouisfed.org/fred/release/dates"
FRED_RELEASE_INFO_URL = "https://api.stlouisfed.org/fred/release"
TRADES_URL = "https://data.alpaca.markets/v2/stocks/trades"

# FRED release id -> our label. The update prints each release's name from FRED, so a wrong id shows.
RELEASES = {
    50: ("jobs", "employment"),  # Employment Situation
    10: ("cpi", "consumer price"),  # Consumer Price Index
    53: ("gdp", "gross domestic"),  # Gross Domestic Product
    54: ("pce", "personal income"),  # Personal Income and Outlays
    9: ("retail", "retail"),  # Advance Monthly Sales for Retail and Food Services
}  # (label, a word the release's name must contain: a wrong id is refused, not saved under the wrong label)

# Days to classify trade by trade (about 1,000 requests each for SPY): the largest moves of the
# last decade and a few ordinary sessions for comparison.
FLOW_SYMBOL = "SPY"
FLOW_DAYS = (
    date(2018, 2, 5), date(2018, 12, 24), date(2020, 3, 9), date(2020, 3, 12), date(2020, 3, 13), date(2020, 3, 16),
    date(2020, 3, 24), date(2022, 6, 13), date(2024, 8, 5), date(2025, 4, 3), date(2025, 4, 4), date(2025, 4, 9),
    date(2019, 6, 4), date(2021, 6, 8), date(2023, 7, 11), date(2024, 5, 14), date(2025, 9, 9),
)  # fmt: skip


# --- scheduled releases (FRED) -------------------------------------------------------------


def too_dense(days: list[date]) -> bool:
    """More than MAX_PER_YEAR dates in some calendar year: a list of every day, not of events."""
    per_year: dict[int, int] = {}
    for d in set(days):
        per_year[d.year] = per_year.get(d.year, 0) + 1
    return max(per_year.values(), default=0) > MAX_PER_YEAR


def parse_release_dates(body: object) -> list[date]:
    if not isinstance(body, dict) or not isinstance(body.get("release_dates"), list):
        raise FetchError("FRED's answer had no release dates")
    out = []
    for row in body["release_dates"]:
        try:
            out.append(date.fromisoformat(row["date"]))
        except (KeyError, ValueError, TypeError):
            continue
    return sorted(set(out))


def fetch_release(release_id: int, key: str) -> tuple[str, list[date]]:
    """(the release's name, its dates from 1990 including those announced) in two requests."""
    common = {"release_id": release_id, "api_key": key, "file_type": "json"}
    info, _ = request_json(f"{FRED_RELEASE_INFO_URL}?{urllib.parse.urlencode(common)}", {}, 30, "FRED")
    releases = info.get("releases") if isinstance(info, dict) else None
    name = releases[0].get("name", "?") if releases else "?"
    query = urllib.parse.urlencode(
        common | {"realtime_start": "1990-01-01", "realtime_end": "9999-12-31", "include_release_dates_with_no_data": "true", "sort_order": "asc", "limit": 10000}
    )
    body, _ = request_json(f"{FRED_RELEASE_URL}?{query}", {}, 30, "FRED")
    return name, parse_release_dates(body)


class Releases:
    def __init__(self, store: Store, key: str | None, fetch: Callable[[int, str], tuple[str, list[date]]] = fetch_release) -> None:
        self.key = key
        self._fetch = fetch
        self._db = store.connection
        self._db.execute("CREATE TABLE IF NOT EXISTS releases (kind TEXT NOT NULL, day TEXT NOT NULL, PRIMARY KEY (kind, day))")
        for (kind,) in self._db.execute("SELECT DISTINCT kind FROM releases").fetchall():  # an earlier run saved a release that lists every day (FOMC, id 101)
            if too_dense(self.days(kind)):
                with self._db:
                    self._db.execute("DELETE FROM releases WHERE kind = ?", (kind,))

    @property
    def configured(self) -> bool:
        return bool(self.key)

    def update(self, say: Callable[[str], None] = print) -> list[str]:
        assert self.key
        problems = []
        for release_id, (kind, expected) in RELEASES.items():
            try:
                name, days = self._fetch(release_id, self.key)
            except FetchStop:
                raise
            except FetchError as e:
                problems.append(f"release {release_id} ({kind}): {e}")
                continue
            if too_dense(days):
                problems.append(f"release {release_id} ({kind}) lists over {MAX_PER_YEAR} dates in a year: not a calendar of events, not saved")
                continue
            if expected not in name.lower():
                problems.append(f"release {release_id} is called {name!r}, not the {kind} release: not saved (fix RELEASES)")
                continue
            with self._db:
                self._db.executemany("INSERT OR REPLACE INTO releases (kind, day) VALUES (?, ?)", [(kind, d.isoformat()) for d in days])
            say(f"{kind}: {len(days)} dates ({name}; {days[0] if days else '-'} to {days[-1] if days else '-'})")
        return problems

    def days(self, kind: str) -> list[date]:
        return [date.fromisoformat(d) for (d,) in self._db.execute("SELECT day FROM releases WHERE kind = ? ORDER BY day", (kind,))]


# --- earnings history (Alpha Vantage) --------------------------------------------------------


class NoEarnings(FetchError):
    """An answer without quarterly earnings: an unsupported symbol, or Alpha Vantage throttling quietly."""


MAX_MISSES = 3  # tries before a symbol with no answer is left alone


class Burst(FetchError):
    """Alpha Vantage's per-second limit (1 request a second on the free plan): wait a moment and ask again."""


MAX_PER_YEAR = 60  # more dates a year than this is not a calendar of events (FRED's FOMC press release, id 101, lists every calendar day)
EARNINGS_PAUSE_SECONDS = 1.5  # between Alpha Vantage requests
EARNINGS_RETRIES = 3


def parse_earnings_history(body: object, key: str | None = None) -> list[tuple[date, str, float | None, float | None]]:
    """(report day, "pre"/"post"/"", reported EPS, surprise %) rows, oldest first."""
    if not isinstance(body, dict):
        raise FetchError("Alpha Vantage's earnings answer wasn't readable")
    note = body.get("Information") or body.get("Note")
    if note and ("per second" in str(note) or "sparingly" in str(note)):
        raise Burst("Alpha Vantage: too many requests in a second")
    if note:  # the daily limit (25 requests on the free plan) or a bad key
        text = str(note)  # Alpha Vantage's own message repeats the key: masked by value, and by the wording it uses
        if key:
            text = text.replace(key, "***")
        text = re.sub(r"(?i)(api ?key(?: as| is|=)? ?)[A-Za-z0-9]{8,}", r"\1***", text)
        raise FetchStop(f"Alpha Vantage said: {text[:300]} (the free plan allows 25 requests a day; a bad key looks the same)")
    rows = body.get("quarterlyEarnings")
    if not isinstance(rows, list):
        raise NoEarnings("Alpha Vantage sent no quarterly earnings for this symbol")

    def number(x: object) -> float | None:
        try:
            return float(x)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None

    out = []
    for row in rows:
        try:
            day = date.fromisoformat(row["reportedDate"])
        except (KeyError, ValueError, TypeError):
            continue
        when = {"pre-market": "pre", "post-market": "post"}.get(str(row.get("reportTime", "")).lower(), "")
        out.append((day, when, number(row.get("reportedEPS")), number(row.get("surprisePercentage"))))
    return sorted(out)


def fetch_earnings_history(symbol: str, key: str) -> list[tuple[date, str, float | None, float | None]]:
    query = urllib.parse.urlencode({"function": "EARNINGS", "symbol": symbol, "apikey": key})
    body, _ = request_json(f"{EARNINGS_URL}?{query}", {}, 30, "Alpha Vantage")
    return parse_earnings_history(body, key)


class EarningsHistory:
    def __init__(self, store: Store, key: str | None, fetch: Callable[[str, str], list] = fetch_earnings_history, sleep: Callable[[float], None] = time.sleep) -> None:
        self.key = key
        self._fetch = fetch
        self._sleep = sleep
        self._db = store.connection
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS earnings_history (symbol TEXT NOT NULL, day TEXT NOT NULL, time TEXT NOT NULL, eps REAL, surprise REAL, PRIMARY KEY (symbol, day))"
        )
        self._db.execute("CREATE TABLE IF NOT EXISTS earnings_fetched (symbol TEXT PRIMARY KEY, at TEXT NOT NULL)")
        self._db.execute("CREATE TABLE IF NOT EXISTS earnings_misses (symbol TEXT PRIMARY KEY, tries INTEGER NOT NULL)")

    @property
    def configured(self) -> bool:
        return bool(self.key)

    def due(self, symbols: list[str]) -> list[str]:
        have = {s for (s,) in self._db.execute("SELECT symbol FROM earnings_fetched")}
        gave_up = {s for (s,) in self._db.execute("SELECT symbol FROM earnings_misses WHERE tries >= ?", (MAX_MISSES,))}
        return [s for s in symbols if s not in have and s not in gave_up]

    def update(self, symbols: list[str], say: Callable[[str], None] = print) -> list[str]:
        """Each symbol not yet fetched; stops at the daily limit (run it again tomorrow)."""
        assert self.key
        problems = []
        for n, symbol in enumerate(self.due(symbols)):
            if n:
                self._sleep(EARNINGS_PAUSE_SECONDS)  # before every request but the first, whatever the last one did
            rows = None
            try:
                for attempt in range(EARNINGS_RETRIES):
                    try:
                        rows = self._fetch(symbol, self.key)
                        break
                    except Burst:
                        if attempt == EARNINGS_RETRIES - 1:
                            raise
                        self._sleep(2.0 * (attempt + 1))
            except Burst as e:
                problems.append(f"{symbol}: {e} even after waiting; run it again in a minute")
                break
            except FetchStop as e:
                problems.append(f"{e} (what is saved is kept; run it again later)")
                break
            except NoEarnings as e:
                with self._db:
                    self._db.execute("INSERT INTO earnings_misses (symbol, tries) VALUES (?, 1) ON CONFLICT(symbol) DO UPDATE SET tries = tries + 1", (symbol,))
                tries = self._db.execute("SELECT tries FROM earnings_misses WHERE symbol = ?", (symbol,)).fetchone()[0]
                problems.append(f"{symbol}: {e} (try {tries} of {MAX_MISSES}; after that it is left alone)")
                continue
            except FetchError as e:
                problems.append(f"{symbol}: {e}")
                continue
            with self._db:
                self._db.executemany(
                    "INSERT OR REPLACE INTO earnings_history (symbol, day, time, eps, surprise) VALUES (?, ?, ?, ?, ?)",
                    [(symbol, d.isoformat(), t, eps, s) for d, t, eps, s in rows],
                )
                self._db.execute("INSERT OR REPLACE INTO earnings_fetched (symbol, at) VALUES (?, ?)", (symbol, datetime.now(UTC).isoformat()))
            say(f"{symbol}: {len(rows)} reports")
        return problems

    def days(self, symbol: str) -> list[date]:
        return [date.fromisoformat(d) for (d,) in self._db.execute("SELECT day FROM earnings_history WHERE symbol = ? ORDER BY day", (symbol,))]


# --- buy and sell volume (Alpaca trades, tick test) -----------------------------------------


# Trades that must not set the tick-test direction or count: out of sequence, extended hours, odd lots, derivatively or
# average priced, bunched, contingent, prior reference price, and the opening/closing/reopening cross prints (their
# volume is not counted either, so the closing cross is missing from the totals).
SKIP_CONDITIONS = frozenset("ZUTI47BVWPMQO56")


def classify(trades: list[dict], last_price: float | None = None, last_side: int = 0) -> tuple[dict[date, tuple[list[int], list[int]]], float | None, int]:
    """Buy and sell volume per regular-session minute slot by day, from trades in time order.
    Returns the days, and the last price and side so the next page continues the sequence."""
    days: dict[date, tuple[list[int], list[int]]] = {}
    for t in trades:
        if SKIP_CONDITIONS.intersection(t.get("c") or ()):
            continue
        price, size = float(t["p"]), int(t.get("s") or 0)
        if last_price is not None:
            if price > last_price:
                last_side = 1
            elif price < last_price:
                last_side = -1
        last_price = price
        moment = datetime.fromisoformat(t["t"]).astimezone(MARKET_TZ)
        slot = moment.hour * 60 + moment.minute - OPEN_MINUTE
        if not 0 <= slot < SLOTS or last_side == 0:
            continue  # outside 9:30-16:00, or no direction known yet
        buy, sell = days.setdefault(moment.date(), ([0] * SLOTS, [0] * SLOTS))
        (buy if last_side > 0 else sell)[slot] += size
    return days, last_price, last_side


# A day in progress: the next page's token, the tick test's last price and side, and the sums so far.
Partial = tuple[str | None, float | None, int, list[int], list[int]]
Trades = Callable[[str, date, Credentials, datetime, Partial | None, Callable[[Partial], None] | None], tuple[list[int], list[int]]]


def fetch_flow_day(
    symbol: str, day: date, creds: Credentials, now: datetime, partial: Partial | None = None,
    checkpoint: Callable[[Partial], None] | None = None, limit: RateLimit = LIMIT,
) -> tuple[list[int], list[int]]:  # fmt: skip
    """One session's buy and sell volume by minute (pages of 10,000 trades: a thousand requests or more for SPY).
    The window starts at 9:25 so the tick test has a price before the open. `checkpoint` is called after each page
    so a dropped connection or a rate limit resumes from `partial` instead of page 1."""
    start = datetime(day.year, day.month, day.day, 9, 25, tzinfo=MARKET_TZ).astimezone(UTC)
    close = datetime(day.year, day.month, day.day, 16, 1, tzinfo=MARKET_TZ).astimezone(UTC)
    if close > now - SIP_DELAY:
        raise FetchError(f"{day} is not over yet (the feed runs about 20 minutes behind)")
    end = close
    token, last_price, last_side, buy, sell = partial or (None, None, 0, [0] * SLOTS, [0] * SLOTS)
    while True:
        query = {"symbols": symbol, "start": start.isoformat().replace("+00:00", "Z"), "end": end.isoformat().replace("+00:00", "Z"), "limit": 10000, "feed": "sip"}
        if token:
            query["page_token"] = token
        limit.wait()
        body, headers = request_json(f"{TRADES_URL}?{urllib.parse.urlencode(query)}", auth_headers(creds), 30)
        limit.update(headers)
        if not isinstance(body, dict):
            raise FetchError("unexpected response for trades")
        days, last_price, last_side = classify((body.get("trades") or {}).get(symbol, []), last_price, last_side)
        b_day, s_day = days.get(day, ([0] * SLOTS, [0] * SLOTS))
        buy = [x + y for x, y in zip(buy, b_day, strict=True)]
        sell = [x + y for x, y in zip(sell, s_day, strict=True)]
        token = body.get("next_page_token")
        if checkpoint and token:
            checkpoint((token, last_price, last_side, buy, sell))
        if not token:
            return buy, sell


class Flow:
    def __init__(self, store: Store, creds: Credentials | None, fetch: Trades = fetch_flow_day) -> None:
        self.creds = creds
        self._fetch = fetch
        self._db = store.connection
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS flow (symbol TEXT NOT NULL, day TEXT NOT NULL, buy BLOB NOT NULL, sell BLOB NOT NULL, PRIMARY KEY (symbol, day))"
        )
        self._db.execute("CREATE TABLE IF NOT EXISTS flow_partial (symbol TEXT NOT NULL, day TEXT NOT NULL, state TEXT NOT NULL, PRIMARY KEY (symbol, day))")

    @property
    def configured(self) -> bool:
        return self.creds is not None

    def saved(self, symbol: str) -> set[date]:
        return {date.fromisoformat(d) for (d,) in self._db.execute("SELECT day FROM flow WHERE symbol = ?", (symbol,))}

    def update(self, symbol: str, days: tuple[date, ...], now: datetime, say: Callable[[str], None] = print) -> list[str]:
        """Each day not yet saved, one at a time (each saved before the next, so a rate limit keeps the rest)."""
        assert self.creds
        problems = []
        for day in sorted(set(days) - self.saved(symbol)):
            say(f"{symbol} {day} trades...")
            row = self._db.execute("SELECT state FROM flow_partial WHERE symbol = ? AND day = ?", (symbol, day.isoformat())).fetchone()
            partial = None
            if row:
                token, price, side, b, s = json.loads(row[0])
                partial = (token, price, side, b, s)
                say("  resuming after a stop")

            def checkpoint(state: Partial, symbol=symbol, day=day) -> None:
                with self._db:
                    self._db.execute("INSERT OR REPLACE INTO flow_partial (symbol, day, state) VALUES (?, ?, ?)", (symbol, day.isoformat(), json.dumps(state)))

            try:
                buy, sell = self._fetch(symbol, day, self.creds, now, partial, checkpoint)
            except FetchStop as e:
                problems.append(f"{day}: {e}")
                break
            except FetchError as e:
                problems.append(f"{symbol} {day}: {e}")
                continue
            if not any(buy) and not any(sell):
                problems.append(f"{symbol} {day}: no trades returned")
                continue
            with self._db:
                self._db.execute(
                    "INSERT OR REPLACE INTO flow (symbol, day, buy, sell) VALUES (?, ?, ?, ?)",
                    (symbol, day.isoformat(), struct.pack(f"<{SLOTS}I", *[min(v, 2**32 - 1) for v in buy]), struct.pack(f"<{SLOTS}I", *[min(v, 2**32 - 1) for v in sell])),
                )
                self._db.execute("DELETE FROM flow_partial WHERE symbol = ? AND day = ?", (symbol, day.isoformat()))
            say(f"{symbol} {day}: buy {sum(buy):,} sell {sum(sell):,} ({sum(buy) / max(sum(buy) + sum(sell), 1):.1%} buys)")
        return problems

    def day(self, symbol: str, day: date) -> tuple[list[int], list[int]] | None:
        row = self._db.execute("SELECT buy, sell FROM flow WHERE symbol = ? AND day = ?", (symbol, day.isoformat())).fetchone()
        return (list(struct.unpack(f"<{SLOTS}I", row[0])), list(struct.unpack(f"<{SLOTS}I", row[1]))) if row else None

