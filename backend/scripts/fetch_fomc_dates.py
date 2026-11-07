"""FOMC meeting dates from the Fed's own calendar pages -> `src/paper_trade/market/fomc_dates.json`.

    uv run python scripts/fetch_fomc_dates.py --dir DIR [--fetch]

`--fetch` downloads federalreserve.gov's calendar page (2021 on) and the historical page of each
year 1993-2020 into DIR (one request a second); without it, DIR must already hold `calendar.html`
and `h<year>.html`. The decision day is the last day of a meeting (the statement comes out at 2:00 PM,
or 2:15 PM in some years, that day). Scheduled meetings are kept apart from conference calls
(unscheduled ones are often emergencies: March 2020) and notation votes are left out.
The file is committed; nothing fetches it at run time.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import time
import urllib.request
from datetime import date
from pathlib import Path

MONTHS = {m: i for i, m in enumerate(("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"), 1)}
MONTHS |= {m[:3]: i for m, i in list(MONTHS.items())}  # the calendar page writes "Jan/Feb 31-1" and "Oct/Nov 31-1"
MONTH = "|".join(sorted(MONTHS, key=len, reverse=True))
CALENDAR_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
HISTORICAL_URL = "https://www.federalreserve.gov/monetarypolicy/fomchistorical{year}.htm"
FIRST_YEAR, LAST_HISTORICAL = 1993, 2020
# a date as the pages write it: "January 28-29", "March 15", "April 30-May 1", "January 29/30", "Jan/Feb 31-1"
WHEN = rf"(?:({MONTH})/({MONTH}) (\d{{1,2}})-(\d{{1,2}})|({MONTH}) (\d{{1,2}})(?:\s*[-/–]\s*(?:({MONTH}) )?(\d{{1,2}}))?)"
QUALIFIER = r"(?: \((unscheduled|cancelled)\))?"
# "January 28-29 Meeting - 2020", "March 15 (unscheduled) Meeting - 2020", "March 15 Conference Call - 2020"
ENTRY = re.compile(rf"{WHEN}{QUALIFIER} (Meeting|Conference Call) - (\d{{4}})")
# the calendar page (2021 on): "2025 FOMC Meetings January 28-29 Statement: ... March 18-19* Statement: ..."
BLOCK = re.compile(r"(\d{4}) FOMC Meetings (.*?)(?= \d{4} FOMC Meetings|$)")
# (meetings still ahead have no links yet: they are followed by the next date or the end; "Released February 18, 2026" is not a meeting)
SCHEDULED = re.compile(rf"(?<!Released ){WHEN}\*?{QUALIFIER}(?!,)(?= Statement| Implementation|\s*$|\s+\*|\s+(?:{MONTH}) \d)")


def decision_day(year: int, g: tuple[str, ...]) -> date:
    """The decision day (the last day of the meeting) from the groups of WHEN."""
    if g[0]:  # "Jan/Feb 31-1": the second month, its day
        return date(year, MONTHS[g[1]], int(g[3]))
    month, first, month2, last = g[4], g[5], g[6], g[7]
    if last:
        return date(year, MONTHS[month2 or month], int(last))
    return date(year, MONTHS[month], int(first))


def text_of(path: Path) -> str:
    raw = path.read_text(encoding="utf-8-sig")
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw)))


def parse_historical(txt: str, year: int) -> tuple[list[date], list[date]]:
    """(meetings, unscheduled meetings and conference calls) of one year; a cancelled meeting is left out."""
    meetings, calls = set(), set()
    for m in ENTRY.finditer(txt):
        g = m.groups()
        qualifier, kind, y = g[8], g[9], int(g[10])
        if y != year or qualifier == "cancelled":
            continue
        (meetings if kind == "Meeting" and qualifier is None else calls).add(decision_day(year, g[:8]))
    return sorted(meetings), sorted(calls)


def parse_calendar(txt: str) -> tuple[list[date], list[date]]:
    meetings, calls = set(), set()
    for y, body in BLOCK.findall(txt):
        for m in SCHEDULED.finditer(body):
            g = m.groups()
            if g[8] == "cancelled":
                continue
            (calls if g[8] else meetings).add(decision_day(int(y), g[:8]))
    return sorted(meetings), sorted(calls)


def fetch(url: str, path: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "paper-trade FOMC calendar script"})
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        path.write_bytes(response.read())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", type=Path, required=True)
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--out", type=Path, default=Path(__file__).parent.parent / "src" / "paper_trade" / "market" / "fomc_dates.json")
    a = ap.parse_args()
    if a.fetch:
        a.dir.mkdir(parents=True, exist_ok=True)
        fetch(CALENDAR_URL, a.dir / "calendar.html")
        for year in range(FIRST_YEAR, LAST_HISTORICAL + 1):
            time.sleep(1)
            fetch(HISTORICAL_URL.format(year=year), a.dir / f"h{year}.html")
    meetings: set[date] = set()
    calls: set[date] = set()
    for year in range(FIRST_YEAR, LAST_HISTORICAL + 1):
        m, c = parse_historical(text_of(a.dir / f"h{year}.html"), year)
        meetings.update(m)
        calls.update(c)
    m, c = parse_calendar(text_of(a.dir / "calendar.html"))
    meetings.update(d for d in m if d.year > LAST_HISTORICAL)
    calls.update(d for d in c if d.year > LAST_HISTORICAL)
    out = {"meetings": [d.isoformat() for d in sorted(meetings)], "calls": [d.isoformat() for d in sorted(calls)]}
    a.out.write_text(json.dumps(out, indent=0) + "\n")
    per_year = {}
    for d in sorted(meetings):
        per_year[d.year] = per_year.get(d.year, 0) + 1
    print(f"{len(meetings)} meetings, {len(calls)} conference calls; meetings a year: {per_year}")


if __name__ == "__main__":
    main()
