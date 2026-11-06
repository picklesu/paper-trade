"""Compare the engine's volume dispersion with the real market's.

    uv run python scripts/check_volume.py --db PATH [--seeds 3] [--years 1.2]

Real: the last 250 sessions of one-minute bars (`intraday`) and the last 3 years of daily
bars (`daily`). Simulated: futures from the real start state (after the newest real day)
for `--seeds` seeds. Per symbol, then the median over symbols and four examples:

* mid-session 5-minute volume: the spread (standard deviation over the mean) of each
  block's share of the day, 11:30 to 14:00 (the real market's bursts show here);
* daily volume: standard deviation of the log, lag-1 and lag-20 autocorrelation, skew;
* the structure of daily volume after removing each symbol's slow level (a 250-day rolling
  median): the autocorrelation at lags 1 to 60, how much of the variance is common to the
  market, the link to the size of the day's move and the day-of-week pattern;
* event days (`calendar.volume_event`): median volume against the previous 60 ordinary
  sessions on quarterly and other monthly expirations, the last session of a month and the
  Russell reconstitution.
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
    tags = [cal.volume_event(d) for d in days]
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


LAGS = (1, 2, 3, 5, 10, 20, 40)


def rolling_median(x: np.ndarray, width: int = 251) -> np.ndarray:
    h = width // 2
    return np.array([np.median(x[max(0, i - h) : i + h + 1]) for i in range(len(x))])


def structure(volume: np.ndarray, close: np.ndarray, days: list[date], trim: int = 0) -> dict:
    """Detrended log volume (days, symbols): variance shares, autocorrelation, link to |return|, weekdays."""
    lv = np.log(volume[1:])
    ret = np.diff(np.log(close), axis=0)
    d = np.column_stack([lv[:, j] - rolling_median(lv[:, j]) for j in range(lv.shape[1])])
    wd = np.array([x.weekday() for x in days[1:]])
    if trim:
        d, ret, wd = d[trim:-trim], ret[trim:-trim], wd[trim:-trim]
    m = d.mean(axis=1)
    ac = lambda x, k: float(np.corrcoef(x[:-k], x[k:])[0, 1])  # noqa: E731
    return {
        "sd": float(np.median(d.std(axis=0))),
        "market share": float(np.mean([np.corrcoef(d[:, j], m)[0, 1] ** 2 for j in range(d.shape[1])])),
        "acf": [float(np.median([ac(d[:, j], k) for j in range(d.shape[1])])) for k in LAGS],
        "move corr": float(np.median([np.corrcoef(d[:, j], np.log(np.abs(ret[:, j]) + 1e-4))[0, 1] for j in range(d.shape[1])])),
        "weekday": [float(d[wd == k].mean()) for k in range(5)],
    }


def real_structure(db: str) -> dict:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    cols = {}
    for sym in NAMES:
        cols[sym] = {date.fromisoformat(r[0]): (r[1], r[2]) for r in con.execute("SELECT day, volume, close FROM daily WHERE symbol=? AND day>='2021-01-04' AND volume>0 ORDER BY day", (sym,))}
    days = sorted(set.intersection(*[set(c) for c in cols.values()]))
    vol = np.array([[cols[s][d][0] for s in NAMES] for d in days], float)
    close = np.array([[cols[s][d][1] for s in NAMES] for d in days], float)
    return structure(vol, close, days)


def sim_structure(db: str, seeds: int) -> dict:
    we.init(db)
    full = RealBars(NAMES, we.G["bars"], we.G["vix"], we.G["series"])
    start = cal.next_trading_day(full.last_day)
    out = []
    for seed in range(seeds):
        engine = MarketEngine(cal.session_open(start), seed=seed, real=full)
        days, vol, close = [], [], []

        def done(day: date, engine=engine, days=days, vol=vol, close=close) -> None:
            sk = engine._skeleton(day)
            days.append(day)
            vol.append(sk.volume.sum(axis=0))
            close.append(np.exp(sk.close[-1]))

        engine.on_day_complete = done
        engine.advance(cal.session_open(cal.add_trading_days(start, 800)))
        out.append(structure(np.array(vol), np.array(close), days, trim=125))
    return {k: np.mean([o[k] for o in out], axis=0) for k in out[0]}


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
    print("\nstructure after removing each symbol's slow level (real: 2021 on; simulated: 800 days from the real start)")
    r, m = real_structure(a.db), sim_structure(a.db, max(2, a.seeds // 2))
    print(f"  {'':22s} {'real':>8s} {'sim':>8s}")
    print(f"  {'sd of log volume':22s} {r['sd']:8.3f} {m['sd']:8.3f}")
    print(f"  {'market-wide share':22s} {r['market share']:8.2f} {m['market share']:8.2f}")
    print(f"  {'corr with log |move|':22s} {r['move corr']:8.2f} {m['move corr']:8.2f}")
    for k, lag in enumerate(LAGS):
        print(f"  {'autocorrelation, lag %d' % lag:22s} {r['acf'][k]:8.2f} {m['acf'][k]:8.2f}")
    print(f"  {'Mon..Fri (log)':22s} {' '.join(f'{x:+.2f}' for x in r['weekday'])}   |   {' '.join(f'{x:+.2f}' for x in m['weekday'])}")
    print("\nevent days (median volume of all 40 symbols against ordinary sessions; real: 2005 on)")
    for k in sorted(set(real["_events"]) | set(sim["_events"])):
        print(f"  {k:24s} real {real['_events'].get(k, float('nan')):.2f}   sim {sim['_events'].get(k, float('nan')):.2f}")


if __name__ == "__main__":
    main()
