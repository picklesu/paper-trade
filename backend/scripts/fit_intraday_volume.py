"""Fit the intraday volume profile from real one-minute bars (`./server.sh update intraday`).

    uv run python scripts/fit_intraday_volume.py --db PATH [--out PATH]

For each symbol with minute bars: the average share of a session's volume in each of the 390
minutes (the 16:00 bar, which holds the closing cross, is folded into the last minute), a tilt
for each regime state (calm / choppy / crisis, the state the model's filter gave that day),
and the main way days differ (a close-heavy against an open-heavy day). Symbols without
minute bars use their tier's average (funds or stocks). Reads the database read-only.
A held-out check (fit before 2023, score after) compares the result with the old U-shape.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import struct
from datetime import date
from pathlib import Path

import numpy as np
from fit_regime import load

from paper_trade.market.regime import STATES, RealRegime, load_params

OUT = Path(__file__).resolve().parents[1] / "src/paper_trade/market/volume_profile.json"
MINUTES = 390
BLOCK = 5  # minutes per block when comparing states and finding the day-to-day mode
BLOCKS = MINUTES // BLOCK
PRIOR_DAYS = 100  # days of a state's own data that outweigh its neighbour state's tilt
SPLIT = date(2023, 1, 1)  # the held-out check fits before and scores after
TIERS = {"fund": ("SPY", "QQQ", "IWM"), "stock": ("AAPL", "NVDA", "JPM", "KO", "GS")}


def blocks(x: np.ndarray) -> np.ndarray:
    return x.reshape(*x.shape[:-1], BLOCKS, BLOCK).sum(axis=-1)


def read_days(con: sqlite3.Connection, symbol: str, regime: RealRegime) -> tuple[list[date], np.ndarray, np.ndarray]:
    """Each full session's shares of its volume by minute, and the regime state that day."""
    days, shares, states = [], [], []
    for d, blob in con.execute("SELECT day, volume FROM intraday WHERE symbol = ? ORDER BY day", (symbol,)):
        v = np.array(struct.unpack("<391I", blob), float)
        if v.sum() == 0 or v[215:].sum() < 0.02 * v.sum():  # no trades, or an early close
            continue
        found = regime.at(day := date.fromisoformat(d))
        if found is None:
            continue
        s = v.copy()
        s[389] += s[390]  # the closing cross joins the last minute
        days.append(day)
        shares.append(s[:MINUTES] / v.sum())
        states.append(found[0])
    return days, np.array(shares), np.array(states)


def normalize(p: np.ndarray) -> np.ndarray:
    return p / p.sum(axis=-1, keepdims=True)


def fit_symbol(S: np.ndarray, st: np.ndarray) -> dict:
    """Profile per state (a tilt from the all-days average, each state leaning on the calmer
    one when it has few days), plus the leading way days differ."""
    base = S.mean(axis=0)
    bb = blocks(base)
    prev = np.zeros(BLOCKS)
    tilts, counts = [], []
    for k in range(len(STATES)):
        n = int((st == k).sum())
        counts.append(n)
        if n == 0:
            tilt = prev
        else:
            own = np.log(blocks(S[st == k].mean(axis=0)) / bb)
            w = 1.0 if k == 0 else n / (n + PRIOR_DAYS)
            tilt = w * own + (1 - w) * prev
        tilts.append(tilt)
        prev = tilt
    profiles = normalize(np.array([base * np.repeat(np.exp(t), BLOCK) for t in tilts]))
    # the day-to-day mode: the first principal component of log block shares around the state profile
    pb = blocks(profiles)
    resid = np.log(np.maximum(blocks(S), 1e-6)) - np.log(pb[st])
    resid -= resid.mean(axis=0)
    _, sv, vt = np.linalg.svd(resid, full_matrices=False)
    mode, scores = vt[0], resid @ vt[0]
    if mode[-1] < 0:  # sign: positive means a heavier close
        mode, scores = -mode, -scores
    # minute noise left over: sd of the log of a minute against profile and mode (busy minutes only)
    fitted = np.log(profiles[st]) + np.repeat(scores[:, None] * mode, BLOCK, axis=1)
    fitted -= np.log(np.exp(fitted).sum(axis=1, keepdims=True))
    with np.errstate(divide="ignore"):
        err = np.where(S > 0, np.log(np.maximum(S, 1e-12)) - fitted, np.nan)
    mid = err[:, 30:360]
    sd = float(np.nanstd(mid))
    centered = mid - np.nanmean(mid, axis=1, keepdims=True)
    ar1 = float(np.nanmean(centered[:, 1:] * centered[:, :-1]) / np.nanmean(centered**2))
    return {
        "days": counts,
        "profile": np.round(profiles, 7).tolist(),
        "mode": np.round(mode, 4).tolist(),
        "mode_sd": round(float(scores.std()), 4),
        "mode_share": round(float(sv[0] ** 2 / (sv**2).sum()), 3),
        "minute_sd": round(sd, 3),
        "minute_ar1": round(ar1, 3),
        "zero_minutes": round(float((S == 0).mean()), 4),
    }


def tier_average(fits: dict[str, dict], names: tuple[str, ...]) -> dict:
    have = [fits[n] for n in names if n in fits]
    out = {"days": [sum(f["days"][k] for f in have) for k in range(len(STATES))]}
    out["profile"] = normalize(np.mean([f["profile"] for f in have], axis=0)).round(7).tolist()
    for key in ("mode", "mode_sd", "mode_share", "minute_sd", "minute_ar1", "zero_minutes"):
        out[key] = np.mean([f[key] for f in have], axis=0).round(4).tolist()
    return out


def u_shape() -> np.ndarray:
    x = (np.arange(MINUTES) + 0.5) / MINUTES
    return normalize(1 + 2 * (2 * x - 1) ** 2)


def held_out(data: dict[str, tuple[list[date], np.ndarray, np.ndarray]]) -> None:
    """Fit before SPLIT; score the blocks' log shares after it against the old U-shape,
    the symbol's average profile, and the profile for the day's regime state."""
    print(f"\nHeld out: fit before {SPLIT}, score after (mean squared error of 5-minute blocks' log share; closing share = last 5 minutes)")
    print(f"{'':6s} {'U-shape':>9s} {'average':>9s} {'by state':>9s}   closing share: real  U-shape  average  by state")
    totals = np.zeros(3)
    for symbol, (days, S, st) in data.items():
        before = np.array([d < SPLIT for d in days])
        fit = fit_symbol(S[before], st[before])
        prof = np.array(fit["profile"])
        test, ts = S[~before], st[~before]
        average = S[before].mean(axis=0)
        err = []
        for p in (np.tile(u_shape(), (len(test), 1)), np.tile(average, (len(test), 1)), prof[ts]):
            err.append(float(np.mean((np.log(np.maximum(blocks(test), 1e-6)) - np.log(blocks(p))) ** 2)))
        totals += err
        close = [float(test[:, -5:].sum(axis=1).mean()), float(u_shape()[-5:].sum()), float(average[-5:].sum()), float(prof[ts][:, -5:].sum(axis=1).mean())]
        print(f"{symbol:6s} {err[0]:9.3f} {err[1]:9.3f} {err[2]:9.3f}   {close[0]:18.3f} {close[1]:8.3f} {close[2]:8.3f} {close[3]:8.3f}")
    print(f"{'mean':6s} {totals[0] / len(data):9.3f} {totals[1] / len(data):9.3f} {totals[2] / len(data):9.3f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()
    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    spy = con.execute("SELECT day, close FROM daily WHERE symbol = 'SPY' ORDER BY day").fetchall()
    _, _, _, series = load(args.db)
    regime = RealRegime(load_params(), [date.fromisoformat(d) for d, _ in spy], np.array([c for _, c in spy]), series)
    names = [s for tier in TIERS.values() for s in tier]
    data = {n: read_days(con, n, regime) for n in names if con.execute("SELECT 1 FROM intraday WHERE symbol = ? LIMIT 1", (n,)).fetchone()}
    if not data:
        raise SystemExit("no minute bars: run ./server.sh update intraday first")
    fits = {n: fit_symbol(S, st) for n, (_, S, st) in data.items()}
    for n, f in fits.items():
        p = np.array(f["profile"])
        print(f"{n:5s} days {f['days']}  closing 5m share by state {[round(float(x[-5:].sum()), 3) for x in p]}  first 30m {[round(float(x[:30].sum()), 3) for x in p]}"
              f"  mode {f['mode_share']:.0%} (sd {f['mode_sd']})  minute sd {f['minute_sd']} ar1 {f['minute_ar1']} zeros {f['zero_minutes']:.1%}")
    held_out(data)
    out = {"states": list(STATES), "minutes": MINUTES, "block": BLOCK, "symbols": fits,
           "tiers": {t: tier_average(fits, names) for t, names in TIERS.items()}}
    Path(args.out).write_text(json.dumps(out, separators=(",", ":")))
    print("saved", args.out)


if __name__ == "__main__":
    main()
