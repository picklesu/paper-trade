"""Trading session calendar: 9:30 AM to 4:00 PM New York time, Monday to Friday.

The sim market only ever exists inside a session. Any time outside one snaps
forward to the next open, and reaching the close continues at the next open.
Exchange holidays are not modeled yet.
"""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

MARKET_TZ = ZoneInfo("America/New_York")
OPEN = time(9, 30)
CLOSE = time(16, 0)
SESSION_SECONDS = 6 * 3600 + 30 * 60
SESSION_MINUTES = SESSION_SECONDS // 60


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5


def next_trading_day(d: date) -> date:
    d += timedelta(days=1)
    while not is_trading_day(d):
        d += timedelta(days=1)
    return d


def session_open(d: date) -> datetime:
    return datetime.combine(d, OPEN, tzinfo=MARKET_TZ).astimezone(UTC)


def session_close(d: date) -> datetime:
    return datetime.combine(d, CLOSE, tzinfo=MARKET_TZ).astimezone(UTC)


def market_day(t: datetime) -> date:
    return t.astimezone(MARKET_TZ).date()


def snap(t: datetime) -> datetime:
    """The time itself if the market is open then, otherwise the next open."""
    d = market_day(t)
    if is_trading_day(d):
        if t < session_open(d):
            return session_open(d)
        if t < session_close(d):
            return t.astimezone(UTC)
    return session_open(next_trading_day(d))


def session_seconds(t: datetime) -> float:
    """Seconds since the open of the session `t` is in."""
    return (t - session_open(market_day(t))).total_seconds()


def add_trading_seconds(t: datetime, seconds: float) -> datetime:
    """Move forward by `seconds` of open-market time, skipping closed periods."""
    t = snap(t)
    while True:
        left = (session_close(market_day(t)) - t).total_seconds()
        if seconds < left:
            return t + timedelta(seconds=seconds)
        seconds -= left
        t = session_open(next_trading_day(market_day(t)))


def trading_days_between(a: date, b: date) -> int:
    """Number of trading days d with a < d <= b."""
    n = 0
    while a < b:
        a = next_trading_day(a)
        n += a <= b
    return n
