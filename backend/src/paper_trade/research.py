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
  so it is less exact than Lee-Ready (about 75-80% of trades right); it shows the day's
  imbalance, not who caused what.
"""

import struct
import urllib.parse
from collections.abc import Callable
from contextlib import contextmanager
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
    50: "jobs",  # Employment Situation
    10: "cpi",  # Consumer Price Index
    101: "fomc",  # FOMC Press Release
    53: "gdp",  # Gross Domestic Product
    54: "pce",  # Personal Income and Outlays
    9: "retail",  # Advance Monthly Sales for Retail and Food Services
}

# Days to classify trade by trade (about 1,000 requests each for SPY): the largest moves of the
# last decade and a few ordinary sessions for comparison.
FLOW_SYMBOL = "SPY"
FLOW_DAYS = (
    date(2018, 2, 5), date(2018, 12, 24), date(2020, 3, 9), date(2020, 3, 12), date(2020, 3, 13), date(2020, 3, 16),
    date(2020, 3, 24), date(2022, 6, 13), date(2024, 8, 5), date(2025, 4, 3), date(2025, 4, 4), date(2025, 4, 9),
    date(2019, 6, 4), date(2021, 6, 8), date(2023, 7, 11), date(2024, 5, 14), date(2025, 9, 9),
)  # fmt: skip


def _transaction(db):
    @contextmanager
    def run():
        db.execute("BEGIN")
        try:
            yield
        except BaseException:
            db.execute("ROLLBACK")
            raise
        db.execute("COMMIT")

    return run()


# --- scheduled releases (FRED) -------------------------------------------------------------


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

    @property
    def configured(self) -> bool:
        return bool(self.key)

    def update(self, say: Callable[[str], None] = print) -> list[str]:
        assert self.key
        problems = []
        for release_id, kind in RELEASES.items():
            try:
                name, days = self._fetch(release_id, self.key)
            except FetchStop:
                raise
            except FetchError as e:
                problems.append(f"release {release_id} ({kind}): {e}")
                continue
            with self._db:
                self._db.executemany("INSERT OR REPLACE INTO releases (kind, day) VALUES (?, ?)", [(kind, d.isoformat()) for d in days])
            say(f"{kind}: {len(days)} dates ({name}; {days[0] if days else '-'} to {days[-1] if days else '-'})")
        return problems

    def days(self, kind: str) -> list[date]:
        return [date.fromisoformat(d) for (d,) in self._db.execute("SELECT day FROM releases WHERE kind = ? ORDER BY day", (kind,))]


# --- earnings history (Alpha Vantage) --------------------------------------------------------


def parse_earnings_history(body: object) -> list[tuple[date, str, float | None, float | None]]:
    """(report day, "pre"/"post"/"", reported EPS, surprise %) rows, oldest first."""
    if not isinstance(body, dict):
        raise FetchError("Alpha Vantage's earnings answer wasn't readable")
    note = body.get("Information") or body.get("Note")
    if note:  # the daily limit (25 requests on the free plan) or a bad key
        raise FetchStop(f"Alpha Vantage: {str(note)[:160]}")
    rows = body.get("quarterlyEarnings")
    if not isinstance(rows, list):
        raise FetchError("Alpha Vantage had no quarterly earnings for this symbol")

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
    return parse_earnings_history(body)


class EarningsHistory:
    def __init__(self, store: Store, key: str | None, fetch: Callable[[str, str], list] = fetch_earnings_history) -> None:
        self.key = key
        self._fetch = fetch
        self._db = store.connection
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS earnings_history (symbol TEXT NOT NULL, day TEXT NOT NULL, time TEXT NOT NULL, eps REAL, surprise REAL, PRIMARY KEY (symbol, day))"
        )
        self._db.execute("CREATE TABLE IF NOT EXISTS earnings_fetched (symbol TEXT PRIMARY KEY, at TEXT NOT NULL)")

    @property
    def configured(self) -> bool:
        return bool(self.key)

    def due(self, symbols: list[str]) -> list[str]:
        have = {s for (s,) in self._db.execute("SELECT symbol FROM earnings_fetched")}
        return [s for s in symbols if s not in have]

    def update(self, symbols: list[str], say: Callable[[str], None] = print) -> list[str]:
        """Each symbol not yet fetched; stops at the daily limit (run it again tomorrow)."""
        assert self.key
        problems = []
        for symbol in self.due(symbols):
            try:
                rows = self._fetch(symbol, self.key)
            except FetchStop as e:
                problems.append(f"{e} (what is saved is kept; run it again later)")
                break
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


def classify(trades: list[dict], last_price: float | None = None, last_side: int = 0) -> tuple[dict[date, tuple[list[int], list[int]]], float | None, int]:
    """Buy and sell volume per regular-session minute slot by day, from trades in time order.
    Returns the days, and the last price and side so the next page continues the sequence."""
    days: dict[date, tuple[list[int], list[int]]] = {}
    for t in trades:
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


Trades = Callable[[str, date, Credentials, datetime], tuple[list[int], list[int]]]


def fetch_flow_day(symbol: str, day: date, creds: Credentials, now: datetime, limit: RateLimit = LIMIT) -> tuple[list[int], list[int]]:
    """One session's buy and sell volume by minute (pages of 10,000 trades: about a thousand requests for SPY)."""
    start = datetime(day.year, day.month, day.day, 9, 30, tzinfo=MARKET_TZ).astimezone(UTC)
    end = min(datetime(day.year, day.month, day.day, 16, 1, tzinfo=MARKET_TZ).astimezone(UTC), now - SIP_DELAY)
    buy, sell = [0] * SLOTS, [0] * SLOTS
    token, last_price, last_side = None, None, 0
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
        if not (token := body.get("next_page_token")):
            return buy, sell


class Flow:
    def __init__(self, store: Store, creds: Credentials | None, fetch: Trades = fetch_flow_day) -> None:
        self.creds = creds
        self._fetch = fetch
        self._db = store.connection
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS flow (symbol TEXT NOT NULL, day TEXT NOT NULL, buy BLOB NOT NULL, sell BLOB NOT NULL, PRIMARY KEY (symbol, day))"
        )

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
            try:
                buy, sell = self._fetch(symbol, day, self.creds, now)
            except FetchStop as e:
                problems.append(f"{day}: {e}")
                break
            except FetchError as e:
                problems.append(f"{symbol} {day}: {e}")
                continue
            if not any(buy) and not any(sell):
                problems.append(f"{symbol} {day}: no trades returned")
                continue
            with _transaction(self._db):
                self._db.execute(
                    "INSERT OR REPLACE INTO flow (symbol, day, buy, sell) VALUES (?, ?, ?, ?)",
                    (symbol, day.isoformat(), struct.pack(f"<{SLOTS}I", *[min(v, 2**32 - 1) for v in buy]), struct.pack(f"<{SLOTS}I", *[min(v, 2**32 - 1) for v in sell])),
                )
            say(f"{symbol} {day}: buy {sum(buy):,} sell {sum(sell):,} ({sum(buy) / max(sum(buy) + sum(sell), 1):.1%} buys)")
        return problems

    def day(self, symbol: str, day: date) -> tuple[list[int], list[int]] | None:
        row = self._db.execute("SELECT buy, sell FROM flow WHERE symbol = ? AND day = ?", (symbol, day.isoformat())).fetchone()
        return (list(struct.unpack(f"<{SLOTS}I", row[0])), list(struct.unpack(f"<{SLOTS}I", row[1]))) if row else None

