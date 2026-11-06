"""Compare the engine's volume dispersion with the real market's.

    uv run python scripts/check_volume.py --db PATH [--seeds 3] [--years 1.2]

Real: the last 250 sessions of one-minute bars (`intraday`) and the last 3 years of daily
bars (`daily`). Simulated: futures from the real start state (after the newest real day)
for `--seeds` seeds. Per symbol, then the median over symbols and four examples:

* mid-session 5-minute volume: the spread (standard deviation over the mean) of each
  block's share of the day, 11:30 to 14:00 (the real market's bursts show here);
* daily volume: standard deviation of the log, lag-1 and lag-20 autocorrelation, skew;
* event days: median volume against the previous 60 ordinary sessions on quarterly
  expirations (third Friday of Mar/Jun/Sep/Dec), other third Fridays, the last session of a
  month, and the Russell reconstitution (last Friday of June).
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import walk_forward_engine as we  # noqa: E402
from paper_trade.market import calendar as cal  # noqa: E402
from paper_trade.market.engine import MarketEngine  # noqa: E402
from paper_trade.market.realbars import RealBars  # noqa: E402
from paper_trade.market.symbols import SYMBOLS  # noqa: E402

NAMES = [s.symbol for s in SYMBOLS]
MID = slice(24, 54)  # 5-minute blocks from 11:30 to 14:00
SAMPLE = ("SPY", "AAPL", "KO", "TSLA")


def third_friday(d: date) -> bool:
    return d.weekday() == 4 and 15 <= d.day <= 21


def event(d: date, month_end: bool) -> str | None:
    if d.month == 6 and d.weekday() == 4 and d.day >= 22 and d.day + 7 > 30:
        return "Russell reconstitution"
    if third_friday(d):
        return "quarterly expiration" if d.month in (3, 6, 9, 12) else "monthly expiration"
    if month_end:
        return "month end"
    return None


def block_cv(day_blocks: np.ndarray) -> float:
    """Mean over mid-session blocks of the spread of the block's share of the day."""
    share = day_blocks / day_blocks.sum(axis=1, keepdims=True)
    return float((share[:, MID].std(axis=0) / share[:, MID].mean(axis=0)).mean())


def daily_stats(v: np.ndarray) -> dict:
    lv = np.log(v[v > 0])
    z = (lv - lv.mean()) / lv.std()
    ac = lambda k: float(np.corrcoef(lv[:-k], lv[k:])[0, 1])  # noqa: E731
    return {"sd": float(lv.std()), "ac1": ac(1), "ac20": ac(20), "skew": float((z**3).mean())}


def events(days: list[date], v: np.ndarray, acc: dict[str, list[float]]) -> None:
    last_of_month = {days[i] for i in range(len(days) - 1) if days[i].month != days[i + 1].month}
    tags = [event(d, d in last_of_month) for d in days]
    for i in range(60, len(days)):
        ordinary = [j for j in range(i - 60, i) if tags[j] is None and not cal.is_early_close(days[j])]
        if tags[i] is None or len(ordinary) < 20:
            continue
        base = np.median(v[ordinary])
        if base > 0:
            acc.setdefault(tags[i], []).append(v[i] / base)


def real_stats(db: str) -> dict:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    out: dict[str, dict] = {}
    events_acc: dict[str, list[float]] = {}
    for sym in NAMES:
        rows = con.execute("SELECT volume FROM intraday WHERE symbol=? ORDER BY day DESC LIMIT 250", (sym,)).fetchall()
        blocks = [np.frombuffer(r[0], dtype=np.float32)[:390].reshape(78, 5).sum(axis=1) for r in rows]
        blocks = np.array([b for b in blocks if b.sum() > 0], dtype=float)
        bars = con.execute("SELECT day, volume FROM daily WHERE symbol=? ORDER BY day", (sym,)).fetchall()
        recent = [(date.fromisoformat(d), v) for d, v in bars if d >= (date.today() - timedelta(days=3 * 365)).isoformat() and v > 0]
        out[sym] = {"cv5": block_cv(blocks), **daily_stats(np.array([v for _, v in recent]))}
        long_run = [(date.fromisoformat(d), v) for d, v in bars if d >= "2005-01-01" and v > 0]
        events([d for d, _ in long_run], np.array([v for _, v in long_run], float), events_acc)
    out["_events"] = {k: float(np.median(v)) for k, v in events_acc.items()}
    return out


def sim_stats(db: str, seeds: int, years: float) -> dict:
    we.init(db)
    full = RealBars(NAMES, we.G["bars"], we.G["vix"], we.G["series"])
    start = cal.next_trading_day(full.last_day)
    end = cal.add_trading_days(start, int(252 * years))
    per: dict[str, list[dict]] = {s: [] for s in NAMES}
    events_acc: dict[str, list[float]] = {}
    for seed in range(seeds):
        engine = MarketEngine(cal.session_open(start), seed=seed, real=full)
        days, daily, blocks = [], [], []

        def done(day: date, engine=engine, days=days, daily=daily, blocks=blocks) -> None:
            sk = engine._skeleton(day)
            days.append(day)
            daily.append(sk.volume.sum(axis=0))
            if not cal.is_early_close(day):
                blocks.append(sk.volume.reshape(78, 5, -1).sum(axis=1).T)  # (symbols, 78)

        engine.on_day_complete = done
        engine.advance(cal.session_open(end))
        daily, blocks = np.array(daily), np.array(blocks)
        for i, sym in enumerate(NAMES):
            per[sym].append({"cv5": block_cv(blocks[:, i, :]), **daily_stats(daily[:, i])})
        for i in range(len(NAMES)):
            events(days, daily[:, i], events_acc)
    out = {s: {k: float(np.mean([r[k] for r in rows])) for k in rows[0]} for s, rows in per.items()}
    out["_events"] = {k: float(np.median(v)) for k, v in events_acc.items()}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--years", type=float, default=1.2)
    a = ap.parse_args()
    real, sim = real_stats(a.db), sim_stats(a.db, a.seeds, a.years)
    keys = ("cv5", "sd", "ac1", "ac20", "skew")
    print(f"{'':14s} " + "  ".join(f"{k:>11s}" for k in keys))
    print("               " + "  ".join(f"{'real / sim':>11s}" for _ in keys))
    med = lambda d, k: float(np.median([d[s][k] for s in NAMES]))  # noqa: E731
    print(f"{'median of 40':14s} " + "  ".join(f"{med(real, k):5.2f}/{med(sim, k):5.2f}" for k in keys))
    for s in SAMPLE:
        print(f"{s:14s} " + "  ".join(f"{real[s][k]:5.2f}/{sim[s][k]:5.2f}" for k in keys))
    print("\nevent days (median volume of all 40 symbols against ordinary sessions; real: 2005 on)")
    for k in sorted(set(real["_events"]) | set(sim["_events"])):
        print(f"  {k:24s} real {real['_events'].get(k, float('nan')):.2f}   sim {sim['_events'].get(k, float('nan')):.2f}")


if __name__ == "__main__":
    main()
