"""Event study of scheduled releases: how SPY, volume and the VIX behave on release days.

    uv run python scripts/event_study.py --db PATH [--report scripts/event_study_report.md]

`--db` is a scratch copy of the `daily`, `macro` and `releases` tables (filled by
`./server.sh update history` and `update events`). For each release kind (jobs, CPI, GDP, PCE,
retail sales; FOMC once its dates are fixed) and period it reports, against the 60 sessions
before, never counting another release day:

* the size of the day's close-to-close move, the opening gap and the move after the open
  (all 8:30 AM releases land in the gap), as a multiple of an ordinary day's;
* volume against the previous 60 sessions' median, and the change in the VIX;
* for jobs and CPI, whether the direction of the day's move follows a crude surprise proxy
  (the unemployment rate's change; inflation's acceleration; neither is a forecast error,
  the real expectations are not in the data).

A multiple is the mean over events of (event day's |x| / baseline mean |x|), with a
bootstrap 90% interval. Nothing here is a trading signal: it says which days the simulated
future should treat as busy, and by how much.
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import date
from pathlib import Path

import numpy as np

KINDS = ("jobs", "cpi", "gdp", "pce", "retail", "fomc")
PERIODS = (("1993-2009", date(1993, 1, 1), date(2009, 12, 31)), ("2010-2026", date(2010, 1, 1), date(2026, 12, 31)), ("all", date(1993, 1, 1), date(2026, 12, 31)))
BASE = 60
MAX_PER_YEAR = 60  # a release with more dates than this is not a calendar of events (FRED's FOMC press release lists every day)


def load(db: str):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = con.execute("SELECT day, open, close, volume FROM daily WHERE symbol='SPY' ORDER BY day").fetchall()
    days = [date.fromisoformat(r[0]) for r in rows]
    o, c, v = (np.array([r[i] for r in rows], float) for i in (1, 2, 3))
    vix = {date.fromisoformat(d): x for d, x in con.execute("SELECT day, value FROM macro WHERE series='VIXCLS'")}
    macro = {s: {date.fromisoformat(d): x for d, x in con.execute("SELECT day, value FROM macro WHERE series=?", (s,))} for s in ("UNRATE", "CPIAUCSL")}
    releases = {}
    for kind in KINDS:
        ds = [date.fromisoformat(d) for (d,) in con.execute("SELECT day FROM releases WHERE kind=? ORDER BY day", (kind,))]
        if ds and len(ds) / max(1, (ds[-1].year - ds[0].year + 1)) > MAX_PER_YEAR:
            print(f"skipping {kind}: {len(ds)} dates is more than {MAX_PER_YEAR} a year")
            continue
        releases[kind] = ds
    return days, o, c, v, vix, macro, releases


def boot(x: np.ndarray, n: int = 2000, seed: int = 1) -> tuple[float, float, float]:
    rng = np.random.default_rng(seed)
    means = np.array([x[rng.integers(0, len(x), len(x))].mean() for _ in range(n)])
    return float(x.mean()), float(np.percentile(means, 5)), float(np.percentile(means, 95))


def spearman(a: np.ndarray, b: np.ndarray) -> float:
    ra, rb = np.argsort(np.argsort(a)), np.argsort(np.argsort(b))
    return float(np.corrcoef(ra, rb)[0, 1])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--report", type=Path, default=Path(__file__).with_name("event_study_report.md"))
    a = ap.parse_args()
    days, o, c, v, vix, macro, releases = load(a.db)
    index = {d: i for i, d in enumerate(days)}
    ret = np.r_[np.nan, np.diff(np.log(c))]
    gap = np.r_[np.nan, np.log(o[1:] / c[:-1])]
    intra = np.log(c / o)
    vol_ratio = np.full(len(days), np.nan)
    for i in range(BASE, len(days)):
        vol_ratio[i] = v[i] / np.median(v[i - BASE : i])
    vix_change = np.array([np.log(vix[d] / vix[days[i - 1]]) if d in vix and i and days[i - 1] in vix else np.nan for i, d in enumerate(days)])
    busy = {d for ds in releases.values() for d in ds}
    ordinary = np.array([d not in busy for d in days])

    def multiple(series: np.ndarray, i: int) -> float:
        window = np.arange(i - BASE, i)
        window = window[ordinary[window] & ~np.isnan(series[window])]
        return abs(series[i]) / np.abs(series[window]).mean() if len(window) >= 20 and not np.isnan(series[i]) else np.nan

    out = ["# Scheduled releases: SPY on release days\n", f"Event days are the release dates that fall on a session (1993 on); baseline: the 60 sessions before, without any release day. Multiples are of an ordinary day's size; the interval is a 90% bootstrap over events.\n"]
    for kind, ds in releases.items():
        out += [f"\n## {kind}\n", "| period | events | close-to-close | opening gap | after the open | volume vs 60d median | VIX change (points of log %) |", "|---|---|---|---|---|---|---|"]
        for name, lo, hi in PERIODS:
            idx = [index[d] for d in ds if d in index and lo <= d <= hi and index[d] > BASE]
            if len(idx) < 12:
                continue
            cells = []
            for series in (ret, gap, intra):
                m = np.array([multiple(series, i) for i in idx])
                m = m[~np.isnan(m)]
                mean, l, h = boot(m)
                cells.append(f"{mean:.2f}x ({l:.2f}-{h:.2f})")
            vr = np.array([vol_ratio[i] for i in idx])
            vr = vr[~np.isnan(vr)]
            vx = np.array([vix_change[i] for i in idx])
            vx = vx[~np.isnan(vx)]
            vm, vl, vh = boot(vr)
            xm, xl, xh = boot(vx * 100)
            out.append(f"| {name} | {len(idx)} | {cells[0]} | {cells[1]} | {cells[2]} | {vm:.2f}x ({vl:.2f}-{vh:.2f}) | {xm:+.1f} ({xl:+.1f} to {xh:+.1f}) |")
    # a crude surprise proxy
    out += ["\n## Does the day's direction follow a crude surprise proxy?\n"]

    def proxy_rows(kind: str, series: str, make) -> None:
        pts = []
        for d in releases.get(kind, []):
            if d not in index or index[d] <= BASE:
                continue
            x = make(d)
            if x is not None and not np.isnan(ret[index[d]]):
                pts.append((x, ret[index[d]]))
        if len(pts) >= 30:
            xs, ys = np.array(pts).T
            out.append(f"* {kind} (proxy: {series}): {len(pts)} events, rank correlation of the proxy with the day's return {spearman(xs, ys):+.2f}; the day rose {np.mean(ys[xs > 0] > 0):.0%} of the {int((xs > 0).sum())} times the proxy was positive and {np.mean(ys[xs < 0] > 0):.0%} of the {int((xs < 0).sum())} times it was negative.")

    def month_value(s: dict, d: date, back: int):
        y, m = d.year, d.month - back
        while m <= 0:
            y, m = y - 1, m + 12
        return s.get(date(y, m, 1))

    def unrate_change(d: date):
        a, b = month_value(macro["UNRATE"], d, 1), month_value(macro["UNRATE"], d, 2)
        return None if a is None or b is None else a - b

    def cpi_accel(d: date):
        v0, v1, v2 = (month_value(macro["CPIAUCSL"], d, k) for k in (1, 2, 3))
        return None if None in (v0, v1, v2) else (v0 / v1 - v1 / v2)

    proxy_rows("jobs", "change in the unemployment rate (a rise is bad news)", lambda d: -(unrate_change(d) or np.nan) if unrate_change(d) is not None else None)
    proxy_rows("cpi", "acceleration of month-on-month inflation (a rise is bad news)", lambda d: -cpi_accel(d) if cpi_accel(d) is not None else None)
    a.report.write_text("\n".join(out) + "\n")
    print("\n".join(out))


if __name__ == "__main__":
    main()
