"""Trading session calendar: 9:30 AM to 4:00 PM New York time, Monday to Friday.

The sim market only ever exists inside a session. Any time outside one snaps
forward to the next open, and reaching the close continues at the next open.
Exchange holidays are not modeled yet.
"""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import numpy as np

MARKET_TZ = ZoneInfo("America/New_York")
OPEN = time(9, 30)
CLOSE = time(16, 0)
SESSION_SECONDS = 6 * 3600 + 30 * 60
SESSION_MINUTES = SESSION_SECONDS // 60
ONE_DAY = np.timedelta64(1, "D")


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5


def add_trading_days(d: date, n: int) -> date:
    """The trading day `n` trading days after trading day `d`."""
    return np.busday_offset(np.datetime64(d, "D"), n).astype(object)


def next_trading_day(d: date) -> date:
    """The first trading day after `d` (which need not be a trading day)."""
    return np.busday_offset(np.datetime64(d, "D") + ONE_DAY, 0, roll="forward").astype(object)


def previous_trading_day(d: date) -> date:
    """The last trading day before `d`."""
    return np.busday_offset(np.datetime64(d, "D") - ONE_DAY, 0, roll="backward").astype(object)


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
    """Move forward by `seconds` of open-market time, skipping closed periods.

    Constant time however far it goes: whole sessions are skipped arithmetically.
    """
    t = snap(t)
    sessions, offset = divmod(session_seconds(t) + seconds, SESSION_SECONDS)
    day = add_trading_days(market_day(t), int(sessions))
    return session_open(day) + timedelta(seconds=offset)


def trading_days_between(a: date, b: date) -> int:
    """Number of trading days d with a < d <= b (0 if b <= a)."""
    if b <= a:
        return 0
    return int(np.busday_count(np.datetime64(a, "D") + ONE_DAY, np.datetime64(b, "D") + ONE_DAY))
