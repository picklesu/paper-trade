"""Trading session calendar: 9:30 AM to 4:00 PM New York time, Monday to Friday,
except NYSE holidays.

The sim market only ever exists inside a session. Any time outside one snaps
forward to the next open, and reaching the close continues at the next open.
Holidays follow today's NYSE rules for every year (one-off closures such as
national days of mourning are not included). Early closes at 1:00 PM (the day after
Thanksgiving, Christmas Eve and July 3 when they are weekdays that trade) are sessions
of 210 minutes; today's rules apply to every year, as with holidays.
"""

import json
import math
from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

MARKET_TZ = ZoneInfo("America/New_York")
OPEN = time(9, 30)
CLOSE = time(16, 0)
SESSION_SECONDS = 6 * 3600 + 30 * 60  # a full session (the engine's grid; an early close uses its first part)
SESSION_MINUTES = SESSION_SECONDS // 60
EARLY_CLOSE = time(13, 0)
EARLY_SECONDS = 3 * 3600 + 30 * 60  # 9:30 AM to 1:00 PM
EARLY_MINUTES = EARLY_SECONDS // 60
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


def early_closes(year: int) -> list[date]:
    """Days the market closes at 1:00 PM (when they trade): the day after Thanksgiving,
    Christmas Eve and July 3 (a weekday before a July 4 that is a weekday)."""
    thanksgiving = nth_weekday(year, 11, 3, 4)
    days = [thanksgiving + timedelta(days=1)]
    christmas_eve = date(year, 12, 24)
    if christmas_eve.weekday() < 5:
        days.append(christmas_eve)
    july3 = date(year, 7, 3)
    if july3.weekday() < 4:  # Monday to Thursday: July 4 is a weekday, so July 3 is not the observed holiday
        days.append(july3)
    return days


RULE_HOLIDAYS = frozenset(d for y in range(FIRST_YEAR, LAST_YEAR + 1) for d in nyse_holidays(y))
HOLIDAYS = RULE_HOLIDAYS
_CAL = np.busdaycalendar(holidays=sorted(HOLIDAYS))
_EARLY_ALL = sorted(d for y in range(FIRST_YEAR, LAST_YEAR + 1) for d in early_closes(y))
EARLY: frozenset[date] = frozenset(d for d in _EARLY_ALL if d not in HOLIDAYS)
_EARLY_DAYS = np.array(sorted(EARLY), dtype="datetime64[D]")


def set_overrides(closed: set[date] = frozenset(), opened: set[date] = frozenset()) -> None:  # type: ignore[assignment]
    """One-off closures the rules don't know (`closed`) and rule holidays the market
    traded (`opened`), found from real data. Call before any market is built."""
    global HOLIDAYS, _CAL, EARLY, _EARLY_DAYS
    HOLIDAYS = frozenset((RULE_HOLIDAYS | set(closed)) - set(opened))
    _CAL = np.busdaycalendar(holidays=sorted(HOLIDAYS))
    EARLY = frozenset(d for d in _EARLY_ALL if d not in HOLIDAYS)
    _EARLY_DAYS = np.array(sorted(EARLY), dtype="datetime64[D]")


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


def is_early_close(d: date) -> bool:
    return d in EARLY


def session_minutes(d: date) -> int:
    """Minutes the session of `d` is open: 390, or 210 on an early close."""
    return EARLY_MINUTES if d in EARLY else SESSION_MINUTES


def session_length(d: date) -> int:
    """Seconds the session of `d` is open."""
    return EARLY_SECONDS if d in EARLY else SESSION_SECONDS


def session_close(d: date) -> datetime:
    return datetime.combine(d, EARLY_CLOSE if d in EARLY else CLOSE, tzinfo=MARKET_TZ).astimezone(UTC)


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


def _early_between(a: date, b: date) -> int:
    """Early closes among the trading days d with a <= d < b."""
    lo, hi = np.datetime64(a, "D"), np.datetime64(b, "D")
    return int(np.searchsorted(_EARLY_DAYS, hi, side="left") - np.searchsorted(_EARLY_DAYS, lo, side="left"))


def add_trading_seconds(t: datetime, seconds: float) -> datetime:
    """Move forward by `seconds` of open-market time, skipping closed periods.

    Whole sessions are skipped arithmetically (a few passes however far it goes); an
    early close is shorter than a full session by `SESSION_SECONDS - EARLY_SECONDS`.
    """
    t = snap(t)
    day = market_day(t)
    pos = session_seconds(t) + seconds  # seconds from the open of `day`
    while pos >= session_length(day):
        pos -= session_length(day)
        day = next_trading_day(day)
        n = int(pos // SESSION_SECONDS)  # sessions that certainly fit (none is longer than a full one)
        if n > 0:
            target = add_trading_days(day, n)
            pos -= n * SESSION_SECONDS - _early_between(day, target) * (SESSION_SECONDS - EARLY_SECONDS)
            day = target
    return session_open(day) + timedelta(seconds=pos)


# Measured on 20 years of daily bars of the 40 symbols, against the median of the previous 60 sessions that
# had no such effect (median volume and absolute move): (volume multiple, volatility multiple).
EFFECT_CHRISTMAS_WEEK = (0.66, 0.70)  # December 26 to January 2
EFFECT_THANKSGIVING_EVE = (0.81, 0.78)
EFFECT_BEFORE_HOLIDAY = (0.89, 0.79)  # the session before a weekday holiday
EFFECT_AFTER_HOLIDAY = (0.97, 1.10)  # and the one after


def _closed_weekday_between(a: date, b: date) -> bool:
    """Whether a weekday between the trading days `a` and `b` is a holiday (a weekend alone doesn't count)."""
    x = a + timedelta(days=1)
    while x < b:
        if x.weekday() < 5:
            return True
        x += timedelta(days=1)
    return False


def day_effects(d: date) -> tuple[float, float]:
    """(volume multiple, volatility multiple) of a simulated session around holidays, against a normal
    day. Early closes return (1, 1): their short session carries it (and the engine's own factor)."""
    if d in EARLY:
        return 1.0, 1.0
    if (d.month == 12 and d.day >= 26) or (d.month == 1 and d.day <= 2):
        return EFFECT_CHRISTMAS_WEEK
    if d.month == 11 and d.weekday() == 2 and 22 <= d.day + 1 <= 28:
        return EFFECT_THANKSGIVING_EVE
    if _closed_weekday_between(d, next_trading_day(d)):
        return EFFECT_BEFORE_HOLIDAY
    if _closed_weekday_between(previous_trading_day(d), d):
        return EFFECT_AFTER_HOLIDAY
    return 1.0, 1.0


# Event days that trade more, measured as the median volume of all 40 symbols against the previous
# 60 ordinary sessions (20 years): the quarterly expiration (index rebalancing), other monthly
# expirations, the last session of a month and the Russell reconstitution (last Friday of June).
EVENT_QUARTERLY_EXPIRATION = 1.62
EVENT_MONTHLY_EXPIRATION = 1.10
EVENT_MONTH_END = 1.09
EVENT_RUSSELL = 1.25


def volume_event(d: date) -> str | None:
    """Which volume event a session is, if any: 'quarterly expiration', 'monthly expiration' (the third
    Friday, or the Thursday before when that Friday is a holiday), 'Russell reconstitution' (the last
    Friday of June) or 'month end'. Early closes are none (their short session decides)."""
    if d in EARLY:
        return None
    third_friday = d.weekday() == 4 and 15 <= d.day <= 21
    friday_off = d.weekday() == 3 and 14 <= d.day <= 20 and not is_trading_day(d + timedelta(days=1))
    if third_friday or friday_off:
        return "quarterly expiration" if d.month in (3, 6, 9, 12) else "monthly expiration"
    if d.month == 6 and d.weekday() == 4 and d.day >= 24:
        return "Russell reconstitution"
    if next_trading_day(d).month != d.month:
        return "month end"
    return None


_EVENT_VOLUME = {
    "quarterly expiration": EVENT_QUARTERLY_EXPIRATION,
    "monthly expiration": EVENT_MONTHLY_EXPIRATION,
    "Russell reconstitution": EVENT_RUSSELL,
    "month end": EVENT_MONTH_END,
}


def volume_events(d: date) -> float:
    """Volume multiple of a simulated session on an expiration, month end or reconstitution day."""
    return _EVENT_VOLUME.get(volume_event(d), 1.0)


# Scheduled economic releases (the event study, `scripts/event_study.py`): the size of the day's move and its
# volume against an ordinary session, 1993-2026 (retail sales left out: its effect on moves is gone since 2010).
# kind -> (volume multiple, volatility multiple, releases a year)
RELEASES = {
    "fomc": (1.37, 1.15, 8),
    "jobs": (1.30, 1.25, 12),
    "cpi": (1.26, 1.12, 12),
    "gdp": (1.22, 1.10, 12),
    "pce": (1.20, 1.09, 12),
}
FOMC_FILE = Path(__file__).with_name("fomc_dates.json")


@lru_cache(maxsize=1)
def _fomc_known() -> frozenset[date]:
    """The Fed's meeting decision days (`scripts/fetch_fomc_dates.py`), 1993 to its published horizon."""
    return frozenset(date.fromisoformat(d) for d in json.loads(FOMC_FILE.read_text())["meetings"])


@lru_cache(maxsize=1)
def _fomc_pattern() -> tuple[tuple[int, int], ...]:
    """(month, day) of the last published year's meetings: later years keep the pattern."""
    known = _fomc_known()
    last = max(d.year for d in known if sum(1 for e in known if e.year == d.year) >= 6)
    return tuple(sorted((d.month, d.day) for d in known if d.year == last))


def _fomc(d: date) -> bool:
    if d in _fomc_known():
        return True
    if d.year <= max(x.year for x in _fomc_known()):
        return False
    # beyond the published years: the Wednesday nearest each of the last published year's dates
    for month, day in _fomc_pattern():
        target = date(d.year, month, min(day, 28))
        if d == target + timedelta(days=(2 - target.weekday() + 3) % 7 - 3):
            return True
    return False


def _on_trading_day(rule: date, earlier: bool) -> date:
    return rule if is_trading_day(rule) else (previous_trading_day(rule) if earlier else next_trading_day(rule))


def _month_end_weekday(year: int, month: int, weekday: int) -> date:
    """The last `weekday` of the month; in December the last one by the 23rd (the statistics offices publish before Christmas week)."""
    if month == 12:
        day = date(year, 12, 23)
        return day - timedelta(days=(day.weekday() - weekday) % 7)
    return nth_weekday(year, month, weekday, -1)


def release_days(d: date) -> list[str]:
    """Which scheduled releases fall on session `d`, by rule: jobs on the first Friday (the second when the first
    is the 1st or 2nd), CPI on the 12th (the next session if closed), GDP on the last Thursday and PCE on the last
    Friday of a month, FOMC decisions from the Fed's calendar (then its last year's pattern). Dates of past
    years are the rule's, not the real days (real past sessions carry their own moves)."""
    kinds = []
    if _fomc(d):
        kinds.append("fomc")
    first_friday = nth_weekday(d.year, d.month, 4, 1)
    jobs = first_friday if first_friday.day >= 3 else first_friday + timedelta(days=7)
    if d == _on_trading_day(jobs, True):
        kinds.append("jobs")
    if d == _on_trading_day(date(d.year, d.month, 12), False):
        kinds.append("cpi")
    if d == _on_trading_day(_month_end_weekday(d.year, d.month, 3), True):
        kinds.append("gdp")
    if d == _on_trading_day(_month_end_weekday(d.year, d.month, 4), True):
        kinds.append("pce")
    return kinds


def _normalisers() -> tuple[float, float]:
    """(volume, volatility) of the average session against an ordinary one, from the releases a year, so that the
    average session over a year stays what the model was calibrated to (those days are in its history)."""
    volume = 1 + sum(n * (v - 1) for v, _, n in RELEASES.values()) / TRADING_DAYS_A_YEAR
    variance = 1 + sum(n * (m**2 - 1) for _, m, n in RELEASES.values()) / TRADING_DAYS_A_YEAR
    return volume, math.sqrt(variance)


TRADING_DAYS_A_YEAR = 252
RELEASE_NORM_VOLUME, RELEASE_NORM_SD = _normalisers()


def release_effects(d: date, event_volume: float = 1.0) -> tuple[float, float]:
    """(volume multiple, volatility multiple) of a simulated session for its scheduled releases. Several on one
    day add their excess; every session is divided by the average, so a year's mean volume and variance are unchanged.
    `event_volume` is the day's expiration / month-end / Russell multiple: the busier of the two counts, not both
    (the measured release multiples already include the month-end and Russell days they fall on)."""
    kinds = release_days(d)
    volume = max(1 + sum(RELEASES[k][0] - 1 for k in kinds), event_volume)
    sd = 1 + sum(RELEASES[k][1] - 1 for k in kinds)
    return volume / RELEASE_NORM_VOLUME, sd / RELEASE_NORM_SD


def trading_days_between(a: date, b: date) -> int:
    """Number of trading days d with a < d <= b (0 if b <= a)."""
    if b <= a:
        return 0
    return int(np.busday_count(np.datetime64(a, "D") + ONE_DAY, np.datetime64(b, "D") + ONE_DAY, busdaycal=_CAL))
