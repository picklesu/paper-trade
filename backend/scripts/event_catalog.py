"""A hand-labelled catalog of the large unscheduled market shocks since 1993, measured on SPY and the VIX.

    uv run python scripts/event_catalog.py --db PATH [--report scripts/event_catalog_report.md]

`--db` is a scratch copy of the `daily` and `macro` tables. The causes are labelled from general knowledge of
the period, not looked up: each entry says how sure the label is (`clear`: the move and its cause are
textbook; `probable`: the usual explanation, others contributed; `ambiguous`: a big move without one cause).
The days come from a detector (a SPY move of 4 or more standard deviations of the previous 60 sessions, or 4.5%),
plus the famous days it misses because volatility was already high (late 2008). For each entry the script measures
what the simulator needs: the day's move, the following week, the worst drawdown in the next 60 sessions, the VIX
jump and its 20-session peak, the day's volume against the previous 60 sessions, and how many sessions the
10-day volatility took to fall back within 25% of its level before the event.

Categories: pandemic, credit (banks, sovereign debt, funding), policy (central banks, governments), trade (tariffs),
geopolitical (war, terror), political (elections, referendums), data (an economic number), technical (market
structure: flash crashes, volatility products, carry trades), bubble (valuation), other.
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import date
from pathlib import Path

import numpy as np

# (day, category, sure, what happened)
CATALOG = [
    (date(1994, 2, 4), "policy", "clear", "the Fed's first rate rise in five years, unannounced, starts the 1994 bond rout"),
    (date(1996, 3, 8), "data", "clear", "a jobs report far above forecasts sends bond yields up"),
    (date(1997, 10, 27), "credit", "clear", "the Asian financial crisis reaches Hong Kong; trading halts for the first time"),
    (date(1998, 8, 31), "credit", "clear", "Russia's default and the LTCM hedge fund's losses"),
    (date(1998, 10, 15), "policy", "clear", "the Fed's surprise cut between meetings ends the LTCM panic"),
    (date(2000, 4, 14), "bubble", "clear", "the dot-com bubble's worst week; the Nasdaq falls 10% in a day"),
    (date(2001, 1, 3), "policy", "clear", "the Fed's surprise cut between meetings"),
    (date(2001, 9, 17), "geopolitical", "clear", "markets reopen after the 11 September attacks"),
    (date(2002, 7, 24), "credit", "probable", "a sharp rally off the lows after the WorldCom and Enron accounting scandals"),
    (date(2002, 10, 15), "other", "ambiguous", "a rally off the 2002 bear-market low"),
    (date(2007, 2, 27), "other", "probable", "China's stocks fall 9% and a comment on recession risk; subprime losses begin to show"),
    (date(2008, 9, 29), "credit", "clear", "Congress rejects the bank rescue (TARP) the week after Lehman failed"),
    (date(2008, 10, 13), "policy", "clear", "governments announce bank recapitalisations: the largest rally in decades"),
    (date(2008, 10, 15), "credit", "clear", "credit markets freeze; fear of a recession"),
    (date(2008, 12, 1), "data", "clear", "the NBER dates the recession from December 2007 and manufacturing collapses"),
    (date(2009, 1, 20), "credit", "probable", "fear of bank nationalisation on the day of the presidential inauguration"),
    (date(2009, 3, 23), "policy", "clear", "the Treasury's plan to buy banks' toxic assets: the rally off the 2009 low"),
    (date(2010, 5, 6), "technical", "clear", "the flash crash: the Dow falls 1,000 points in minutes and recovers"),
    (date(2010, 5, 10), "policy", "clear", "the European rescue fund (over $900bn) after Greece"),
    (date(2011, 8, 8), "credit", "clear", "the first US credit rating downgrade (S&P, 5 August) and the euro debt crisis"),
    (date(2014, 7, 31), "geopolitical", "ambiguous", "Argentina's default, Russia sanctions and Gaza"),
    (date(2015, 8, 24), "other", "probable", "China's yuan devaluation and slowdown fear; a flash crash at the open"),
    (date(2016, 6, 24), "political", "clear", "the United Kingdom votes to leave the European Union (Brexit)"),
    (date(2017, 5, 17), "political", "probable", "reports that the president asked the FBI director to drop an investigation"),
    (date(2018, 2, 5), "technical", "clear", "'Volmageddon': inverse-VIX products collapse after a wage-growth scare"),
    (date(2018, 10, 10), "policy", "probable", "bond yields and rate fear, then a technology selloff"),
    (date(2018, 12, 26), "policy", "probable", "the rebound after the Christmas Eve low; fear of the Fed and of a shutdown eases"),
    (date(2019, 5, 13), "trade", "clear", "the United States raises tariffs on China and China retaliates"),
    (date(2019, 8, 5), "trade", "clear", "China lets the yuan pass 7 per dollar; tariffs escalate"),
    (date(2020, 3, 9), "pandemic", "clear", "the coronavirus and the Saudi-Russian oil price war: the first market-wide halt since 1997"),
    (date(2020, 3, 12), "pandemic", "clear", "the coronavirus travel ban from Europe; the worst day since 1987"),
    (date(2020, 3, 16), "pandemic", "clear", "the coronavirus lockdowns begin; a Fed emergency cut does not stop the fall"),
    (date(2020, 3, 24), "policy", "clear", "agreement on the stimulus bill and the Fed's unlimited asset buying"),
    (date(2020, 6, 11), "pandemic", "probable", "second-wave fears and a cautious Fed after a 45% rally"),
    (date(2022, 11, 10), "data", "clear", "a cooler inflation report starts the rally off the 2022 lows"),
    (date(2024, 8, 5), "technical", "clear", "the yen carry trade unwinds after a Bank of Japan rise and weak US jobs"),
    (date(2024, 12, 18), "policy", "clear", "the Fed signals fewer rate cuts in 2025"),
    (date(2025, 4, 3), "trade", "clear", "the 'Liberation Day' tariffs announced"),
    (date(2025, 4, 9), "trade", "clear", "the 90-day tariff pause: the largest daily gain since 2008"),
    (date(2025, 10, 10), "trade", "probable", "a threat of new tariffs on China"),
    (date(2026, 11, 5), "political", "probable", "the day after the US midterm elections (the cause as the user reported it)"),
]  # fmt: skip
PERSIST_BACK = 1.25
MAX_PERSIST = 120
LEVEL_DAYS = 250  # the normal level of volatility: the standard deviation of the 250 sessions before the event
WEEK, WORST, PEAK = 4, 60, 20  # sessions after the event that each measure needs


def load(db: str):
    con = sqlite3.connect(Path(db).resolve().as_uri() + "?mode=ro", uri=True)
    rows = con.execute("SELECT day, close, volume FROM daily WHERE symbol='SPY' ORDER BY day").fetchall()
    days = [date.fromisoformat(r[0]) for r in rows]
    vix = {date.fromisoformat(d): v for d, v in con.execute("SELECT day, value FROM macro WHERE series='VIXCLS'")}
    return days, np.array([r[1] for r in rows], float), np.array([r[2] for r in rows], float), vix


def zscore(ret: np.ndarray, i: int) -> float:
    """The move of session i in standard deviations of the 60 sessions before it."""
    return float(ret[i] / ret[i - 60 : i].std())


def measure(i: int, days, close, volume, vix, ret) -> dict:
    """What followed session i. A measure that needs sessions the data does not have yet is None."""
    n = len(close)
    out = {"ret": float(ret[i]), "z": zscore(ret, i)}
    out["week"] = float(np.log(close[i + WEEK] / close[i - 1])) if i + WEEK < n else None
    out["drawdown"] = float(close[i : i + WORST + 1].min() / close[i - 1] - 1) if i + WORST < n else None
    out["volume"] = float(volume[i] / np.median(volume[i - 60 : i]))
    before, after = vix.get(days[i - 1]), vix.get(days[i])
    out["vix_jump"] = float(after / before - 1) if before and after else float("nan")
    peak = [vix[days[k]] for k in range(i, min(i + PEAK + 1, n)) if days[k] in vix]
    out["vix_peak"] = float(max(peak)) if peak and i + PEAK < n else None
    level = ret[max(1, i - LEVEL_DAYS) : i].std()
    persist = MAX_PERSIST
    for k in range(i + 10, min(i + MAX_PERSIST, n)):  # (from the 10th session on: the shock day is out of the 10-day window)
        if ret[k - 9 : k + 1].std() <= PERSIST_BACK * level:
            persist = k - i
            break
    else:
        if i + MAX_PERSIST > n:
            persist = None  # the data ends first: not known yet
    out["persist"] = persist
    return out


def fmt(x, spec: str) -> str:
    return "not yet" if x is None else format(x, spec)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--report", type=Path, default=Path(__file__).with_name("event_catalog_report.md"))
    a = ap.parse_args()
    days, close, volume, vix = load(a.db)
    index = {d: i for i, d in enumerate(days)}
    ret = np.r_[0.0, np.diff(np.log(close))]
    z = np.array([zscore(ret, i) if i > 61 else np.nan for i in range(len(ret))])
    rows = []
    for day, category, sure, text in CATALOG:
        if day in index and index[day] > LEVEL_DAYS:
            rows.append((day, category, sure, text, measure(index[day], days, close, volume, vix, ret)))
        else:
            print(f"skipped {day}: not in the data or too early for its baselines")
    out = ["# Large unscheduled market shocks, 1993-2026 (SPY)\n"]
    out.append(f"{len(rows)} entries, labelled by hand (`clear`: textbook cause; `probable`; `ambiguous`). Move = the day's log return; week = through the 5th session; worst = the lowest close in the next 60 sessions against the close before; VIX change = the day's change; peak = highest VIX in the next 20 sessions; volume = against the previous 60 sessions' median; back = sessions (from the 10th on) until the 10-day volatility is within 25% of the level of the 250 sessions before the event (120 = not within 120). 'not yet' = the data ends before the measure can be made.\n")
    out.append("| day | category | sure | move | z | week | worst | VIX change | VIX peak | volume | back | what happened |")
    out.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for day, category, sure, text, m in rows:
        out.append(f"| {day} | {category} | {sure} | {m['ret']:+.1%} | {m['z']:+.1f} | {fmt(m['week'], '+.1%')} | {fmt(m['drawdown'], '+.1%')} | {m['vix_jump']:+.0%} | {fmt(m['vix_peak'], '.0f')} | {m['volume']:.1f}x | {fmt(m['persist'], 'd')} | {text} |")
    out.append("\n## By category (falls only: the numbers to calibrate a down shock)\n")
    out.append("| category | entries | falls | mean fall | mean volume | median VIX change | median sessions to calm |")
    out.append("|---|---|---|---|---|---|---|")
    for cat in sorted({r[1] for r in rows}):
        sel = [r for r in rows if r[1] == cat]
        down = [r for r in sel if r[4]["ret"] < 0]
        if not down:
            out.append(f"| {cat} | {len(sel)} | 0 | - | - | - | - |")
            continue
        jumps = [r[4]["vix_jump"] for r in down if not np.isnan(r[4]["vix_jump"])]
        known = [r[4]["persist"] for r in down if r[4]["persist"] is not None]
        out.append(
            f"| {cat} | {len(sel)} | {len(down)} | {np.mean([-r[4]['ret'] for r in down]):.1%} | {np.mean([r[4]['volume'] for r in down]):.1f}x | "
            f"{(f'{np.median(jumps):+.0%}' if jumps else '-')} | {(int(np.median(known)) if known else '-')} |"
        )
    # how much of the detector's list the catalog explains
    detected = [i for i in range(61, len(days)) if abs(z[i]) >= 4 or abs(ret[i]) >= 0.045]
    labelled = {index[r[0]] for r in rows}
    clear = {index[r[0]] for r in rows if r[2] == "clear"}
    near = lambda i, s: any(abs(i - k) <= 3 for k in s)  # noqa: E731
    out.append(f"\n## Coverage\n\nThe detector (a move of 4 or more standard deviations of the previous 60 sessions, or 4.5%) finds {len(detected)} sessions. {sum(near(i, labelled) for i in detected)} are within 3 sessions of a catalog entry, {sum(near(i, clear) for i in detected)} of a `clear` one.")
    rate = {c: sum(1 for r in rows if r[1] == c and r[4]["ret"] < 0) / ((days[-1] - days[0]).days / 365.25) for c in sorted({r[1] for r in rows})}
    out.append(
        "\nFalls a year by category among these hand-picked entries (over the 33 years): " + ", ".join(f"{c} {v:.2f}" for c, v in rate.items())
        + f"; {sum(rate.values()):.2f} in all. This is a count of remembered notable events, not of every large day: it cannot be compared with the engine's rate of all -3% days."
    )
    a.report.write_text("\n".join(out) + "\n")
    print("\n".join(out))


if __name__ == "__main__":
    main()
