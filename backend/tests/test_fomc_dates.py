import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import fetch_fomc_dates as fd  # noqa: E402

HISTORICAL = (
    "2020 January 28-29 Meeting - 2020 Beige Book March 2 (unscheduled) Meeting - 2020 Statement (Released March 3, 2020) March 15 Conference Call - 2020 "
    "March 17-18 (cancelled) Meeting - 2020 March 19 (notation vote) - 2020 April 28-29 Meeting - 2020 Jan/Feb 31-1 Meeting - 2020 April 30-May 1 Meeting - 2020 January 29/30 Meeting - 2020"
)
CALENDAR = (
    "2023 FOMC Meetings Jan/Feb 31-1 Statement: PDF | HTML Minutes: PDF | HTML (Released February 22, 2023) March 21-22* Statement: PDF | HTML "
    "August 22 (notation vote) Statement: PDF Oct/Nov 31-1 Statement: PDF 2027 FOMC Meetings January 27-28 March 16-17* December 7-8* * Meeting associated with a Summary of Economic Projections."
)


def test_historical_page_meetings_calls_and_odd_formats() -> None:
    meetings, calls = fd.parse_historical(HISTORICAL, 2020)
    assert date(2020, 1, 29) in meetings and date(2020, 4, 29) in meetings  # a range: its last day
    assert date(2020, 2, 1) in meetings and date(2020, 5, 1) in meetings and date(2020, 1, 30) in meetings  # "Jan/Feb 31-1", "April 30-May 1", "29/30"
    assert date(2020, 3, 18) not in meetings and date(2020, 3, 19) not in meetings  # cancelled; a notation vote is no meeting
    assert calls == [date(2020, 3, 2), date(2020, 3, 15)]  # unscheduled meetings count with the calls


def test_calendar_page_skips_release_dates_and_votes_and_keeps_meetings_ahead() -> None:
    meetings, calls = fd.parse_calendar(CALENDAR)
    assert meetings == [date(2023, 2, 1), date(2023, 3, 22), date(2023, 11, 1), date(2027, 1, 28), date(2027, 3, 17), date(2027, 12, 8)]
    assert date(2023, 2, 22) not in meetings and date(2023, 8, 22) not in meetings and calls == []


def test_meetings_within_three_days_are_one_decision_and_a_bad_parse_is_refused() -> None:
    import pytest

    kept, calls = fd.settle({date(2003, 9, 15), date(2003, 9, 16), date(2003, 10, 28)}, set())
    assert kept == [date(2003, 9, 16), date(2003, 10, 28)] and calls == [date(2003, 9, 15)]
    with pytest.raises(SystemExit):
        fd.check([date(2020, 1, 4)])  # a Saturday


def test_the_committed_file_is_a_plausible_calendar() -> None:
    path = Path(fd.__file__).resolve().parents[1] / "src" / "paper_trade" / "market" / "fomc_dates.json"
    meetings = [date.fromisoformat(d) for d in json.loads(path.read_text())["meetings"]]
    per_year: dict[int, int] = {}
    for d in meetings:
        per_year[d.year] = per_year.get(d.year, 0) + 1
    assert {y: n for y, n in per_year.items() if y <= 2026 and n != 8} == {2020: 7}, per_year  # eight a year; 2020 had one cancelled and two emergencies (calls)
    assert all(d.weekday() < 5 for d in meetings) and all((b - a).days > 20 for a, b in zip(meetings, meetings[1:], strict=False))
    assert min(per_year) == 1993 and max(per_year) >= 2026 and date(2020, 4, 29) in meetings and date(2008, 12, 16) in meetings and date(2003, 9, 15) not in meetings
