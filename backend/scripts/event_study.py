"""Event study of scheduled releases: how SPY, volume and the VIX behave on release days.

    uv run python scripts/event_study.py --db PATH [--report scripts/event_study_report.md]

`--db` is a scratch copy of the `daily`, `macro` and `releases` tables (filled by
`./server.sh update history` and `update events`). For each release kind (jobs, CPI, GDP, PCE,
retail sales, and FOMC decision days from `market/fomc_dates.json`) and period it reports, against the 60 sessions
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
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from paper_trade.research import too_dense  # noqa: E402

KINDS = ("jobs", "cpi", "gdp", "pce", "retail", "fomc")
PERIODS = (("1993-2009", date(1993, 1, 1), date(2009, 12, 31)), ("2010-2026", date(2010, 1, 1), date(2026, 12, 31)), ("all", date(1993, 1, 1), date(2026, 12, 31)))
BASE = 60


def load(db: str):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = con.execute("SELECT day, open, close, volume FROM daily WHERE symbol='SPY' ORDER BY day").fetchall()
    days = [date.fromisoformat(r[0]) for r in rows]
    o, c, v = (np.array([r[i] for r in rows], float) for i in (1, 2, 3))
    vix = {date.fromisoformat(d): x for d, x in con.execute("SELECT day, value FROM macro WHERE series='VIXCLS'")}
    macro = {s: {date.fromisoformat(d): x for d, x in con.execute("SELECT day, value FROM macro WHERE series=?", (s,))} for s in ("UNRATE", "CPIAUCSL")}
    releases = {}
    fomc_file = Path(__file__).parent.parent / "src" / "paper_trade" / "market" / "fomc_dates.json"  # the Fed's own calendar (fetch_fomc_dates.py)
    if not fomc_file.exists():
        raise SystemExit(f"{fomc_file} is missing: run scripts/fetch_fomc_dates.py")
    fomc = json.loads(fomc_file.read_text())
    releases["fomc"] = [date.fromisoformat(d) for d in fomc["meetings"]]
    releases["_calls"] = [date.fromisoformat(d) for d in fomc["calls"]]  # unscheduled calls: busy days, kept out of every baseline, not reported
    for kind in KINDS:
        if kind == "fomc":
            continue
        ds = [date.fromisoformat(d) for (d,) in con.execute("SELECT day FROM releases WHERE kind=? ORDER BY day", (kind,))]
        if too_dense(ds):
            print(f"skipping {kind}: it lists a date for nearly every day, not events")
            continue
        releases[kind] = ds
    return days, o, c, v, vix, macro, releases


def boot(x: np.ndarray, n: int = 2000, seed: int = 1) -> tuple[float, float, float]:
    if len(x) == 0:
        return float("nan"), float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    means = np.array([x[rng.integers(0, len(x), len(x))].mean() for _ in range(n)])
    return float(x.mean()), float(np.percentile(means, 5)), float(np.percentile(means, 95))


def ranks(x: np.ndarray) -> np.ndarray:
    """Ranks with ties given their average rank (many months leave the unemployment rate unchanged)."""
    order = np.argsort(x, kind="stable")
    r = np.empty(len(x))
    r[order] = np.arange(len(x))
    for value in np.unique(x):
        tied = x == value
        if tied.sum() > 1:
            r[tied] = r[tied].mean()
    return r


def spearman(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.corrcoef(ranks(a), ranks(b))[0, 1])


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
    busy = {d for ds in releases.values() for d in ds}
    ordinary = np.array([d not in busy for d in days])
    vol_ratio = np.full(len(days), np.nan)
    for i in range(BASE, len(days)):
        window = v[i - BASE : i][ordinary[i - BASE : i]]  # the same baseline as the moves: ordinary sessions
        if len(window) >= 20:
            vol_ratio[i] = v[i] / np.median(window)
    vix_change = np.array([np.log(vix[d] / vix[days[i - 1]]) if d in vix and i and days[i - 1] in vix else np.nan for i, d in enumerate(days)])

    def multiple(series: np.ndarray, i: int) -> float:
        window = np.arange(i - BASE, i)
        window = window[ordinary[window] & ~np.isnan(series[window])]
        return abs(series[i]) / np.abs(series[window]).mean() if len(window) >= 20 and not np.isnan(series[i]) else np.nan

    out = ["# Scheduled releases: SPY on release days\n", f"Event days are the release dates that fall on a session (1993 on); baseline: the 60 sessions before, without any release day or unscheduled FOMC call. A day with two releases (an FOMC decision and a CPI) is counted in both tables. Multiples are of an ordinary day's size; the interval is a 90% bootstrap over events.\n"]
    for kind, ds in releases.items():
        if kind.startswith("_"):
            continue
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
        pts, dropped = [], 0
        for d in releases.get(kind, []):
            if d not in index or index[d] <= BASE:
                continue
            x = make(d)
            if x is None or np.isnan(x) or np.isnan(ret[index[d]]):
                dropped += 1  # a missing observation: left out, counted
                continue
            pts.append((x, ret[index[d]]))
        if len(pts) >= 30:
            xs, ys = np.array(pts).T
            up, down = xs > 0, xs < 0
            rate = lambda m: f"{np.mean(ys[m] > 0):.0%}" if m.any() else "n/a"  # noqa: E731
            out.append(
                f"* {kind} (proxy: {series}): {len(pts)} events ({dropped} left out for a missing value), rank correlation of the proxy with the day's return {spearman(xs, ys):+.2f}; "
                f"the day rose {rate(up)} of the {int(up.sum())} times the proxy was positive, {rate(down)} of the {int(down.sum())} times it was negative, and the {int((xs == 0).sum())} unchanged months are in the correlation only."
            )

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

    proxy_rows("jobs", "change in the unemployment rate (a rise is bad news)", lambda d: None if unrate_change(d) is None else -unrate_change(d))
    proxy_rows("cpi", "acceleration of month-on-month inflation (a rise is bad news)", lambda d: None if cpi_accel(d) is None else -cpi_accel(d))
    a.report.write_text("\n".join(out) + "\n")
    print("\n".join(out))


if __name__ == "__main__":
    main()
