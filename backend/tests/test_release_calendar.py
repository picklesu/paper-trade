from datetime import date, timedelta

from paper_trade.market import calendar as cal


def year_days(year: int):
    d = date(year, 1, 1)
    while d.year == year:
        if cal.is_trading_day(d):
            yield d
        d += timedelta(days=1)


def test_release_days_fall_on_sessions_at_the_usual_rates_in_any_year() -> None:
    for year in (2024, 2026, 2029, 2040):
        counts: dict[str, int] = {}
        for d in year_days(year):
            for kind in cal.release_days(d):
                counts[kind] = counts.get(kind, 0) + 1
        assert counts == {"fomc": 8, "jobs": 12, "cpi": 12, "gdp": 12, "pce": 12}, (year, counts)


def test_known_days_and_rules() -> None:
    assert "fomc" in cal.release_days(date(2025, 3, 19)) and "fomc" in cal.release_days(date(2008, 12, 16))  # the Fed's calendar
    assert "jobs" in cal.release_days(date(2026, 12, 4)) and "jobs" not in cal.release_days(date(2026, 10, 2))  # the Friday the 2nd waits a week
    assert "jobs" in cal.release_days(date(2026, 10, 9))
    assert "cpi" in cal.release_days(date(2026, 11, 12)) and "cpi" in cal.release_days(date(2026, 12, 14))  # the 12th is a Saturday: the next session
    future = [d for d in year_days(2035) if "fomc" in cal.release_days(d)]
    assert len(future) == 8 and all(d.weekday() == 2 for d in future)  # beyond the Fed's published years: Wednesdays in the same months
    assert {d.month for d in future} == {d.month for d in year_days(2026) if "fomc" in cal.release_days(d)}


def test_effects_raise_release_days_and_keep_the_year_average() -> None:
    quiet = cal.release_effects(date(2026, 12, 3))
    busy = cal.release_effects(date(2026, 12, 4))  # jobs
    assert quiet[0] < 1 and quiet[1] < 1 and busy[0] > 1.15 and busy[1] > 1.1
    for year in (2026, 2031):
        days = list(year_days(year))
        volume = sum(cal.release_effects(d)[0] for d in days) / len(days)
        variance = sum(cal.release_effects(d)[1] ** 2 for d in days) / len(days)
        assert abs(volume - 1) < 0.02 and abs(variance - 1) < 0.02, (year, volume, variance)  # the model's average session is unchanged
    both = [d for d in year_days(2026) if len(cal.release_days(d)) > 1]
    if both:
        two = cal.release_effects(both[0])  # releases on one day add their excess, they do not multiply
        assert two[1] < (1.25 / cal.RELEASE_NORM_SD) * (1.15 / cal.RELEASE_NORM_SD) * 1.01
