"""Trading session calendar: 9:30 AM to 4:00 PM New York time, Monday to Friday,
except NYSE holidays.

The sim market only ever exists inside a session. Any time outside one snaps
forward to the next open, and reaching the close continues at the next open.
Holidays follow today's NYSE rules for every year (one-off closures such as
national days of mourning are not included). Early closes (1:00 PM on some
days around holidays) are not modeled: those days are full sessions.
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
FIRST_YEAR, LAST_YEAR = 1900, 3000  # the years the app can go to, and a little more


def _easter(year: int) -> date:
    """Western Easter Sunday (the anonymous Gregorian algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    month, day = divmod(h + m - 7 * n + 114, 31)
    return date(year, month, day + 1)


def nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The `n`th `weekday` (0 = Monday) of the month; n = -1 for the last."""
    if n > 0:
        first = date(year, month, 1)
        return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))
    last = date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _observed(d: date, saturday_to_friday: bool = True) -> date | None:
    """A fixed-date holiday on a weekend is observed on Friday (Saturday) or
    Monday (Sunday)."""
    if d.weekday() == 5:
        return d - timedelta(days=1) if saturday_to_friday else None
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def nyse_holidays(year: int) -> list[date]:
    days = [
        # New Year's Day on a Saturday isn't observed (the Friday before ends a year).
        _observed(date(year, 1, 1), saturday_to_friday=False),
        nth_weekday(year, 1, 0, 3),  # Martin Luther King Jr. Day
        nth_weekday(year, 2, 0, 3),  # Washington's Birthday
        _easter(year) - timedelta(days=2),  # Good Friday
        nth_weekday(year, 5, 0, -1),  # Memorial Day
        _observed(date(year, 6, 19)) if year >= 2022 else None,  # Juneteenth
        _observed(date(year, 7, 4)),  # Independence Day
        nth_weekday(year, 9, 0, 1),  # Labor Day
        nth_weekday(year, 11, 3, 4),  # Thanksgiving
        _observed(date(year, 12, 25)),  # Christmas
    ]
    return [d for d in days if d is not None]


RULE_HOLIDAYS = frozenset(d for y in range(FIRST_YEAR, LAST_YEAR + 1) for d in nyse_holidays(y))
HOLIDAYS = RULE_HOLIDAYS
_CAL = np.busdaycalendar(holidays=sorted(HOLIDAYS))


def set_overrides(closed: set[date] = frozenset(), opened: set[date] = frozenset()) -> None:  # type: ignore[assignment]
    """One-off closures the rules don't know (`closed`) and rule holidays the market
    traded (`opened`), found from real data. Call before any market is built."""
    global HOLIDAYS, _CAL
    HOLIDAYS = frozenset((RULE_HOLIDAYS | set(closed)) - set(opened))
    _CAL = np.busdaycalendar(holidays=sorted(HOLIDAYS))


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in HOLIDAYS


def add_trading_days(d: date, n: int) -> date:
    """The trading day `n` trading days after trading day `d`."""
    return np.busday_offset(np.datetime64(d, "D"), n, busdaycal=_CAL).astype(object)


def next_trading_day(d: date) -> date:
    """The first trading day after `d` (which need not be a trading day)."""
    return np.busday_offset(np.datetime64(d, "D") + ONE_DAY, 0, roll="forward", busdaycal=_CAL).astype(object)


def previous_trading_day(d: date) -> date:
    """The last trading day before `d`."""
    return np.busday_offset(np.datetime64(d, "D") - ONE_DAY, 0, roll="backward", busdaycal=_CAL).astype(object)


def trading_day_on_or_before(d: date) -> date:
    return d if is_trading_day(d) else previous_trading_day(d)


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
    return int(np.busday_count(np.datetime64(a, "D") + ONE_DAY, np.datetime64(b, "D") + ONE_DAY, busdaycal=_CAL))
