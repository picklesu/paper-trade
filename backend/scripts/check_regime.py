"""Simulate a fitted regime model for a long time and compare with SPY's history.

    uv run python scripts/check_regime.py --db PATH --params PATH
"""

from __future__ import annotations

import argparse
import json

import numpy as np

from fit_regime import PERIOD, load
from paper_trade.market.regime import STATES, RegimeParams


def stats(r: np.ndarray) -> dict:
    wealth = np.cumsum(r)
    peak = np.maximum.accumulate(wealth)
    dd = 1 - np.exp(wealth - peak)
    # count separate drawdowns deeper than 20%: a new one starts after a new high
    deep, in_dd, worst = 0, False, 0.0
    for d in dd:
        if d > 0.2 and not in_dd:
            deep, in_dd = deep + 1, True
        if d == 0:
            in_dd = False
        worst = max(worst, d)
    years = len(r) * PERIOD / 252
    return {"return/yr": float(r.sum() / years), "vol/yr": float(r.std() * np.sqrt(252 / PERIOD)), "worst dd": round(worst, 2),
            "drawdowns >20% per 20y": round(deep / years * 20, 1), "weekly kurtosis": float(((r - r.mean()) ** 4).mean() / r.var() ** 2)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--params", required=True)
    ap.add_argument("--years", type=int, default=2000)
    args = ap.parse_args()
    p = RegimeParams.from_json(json.load(open(args.params)))
    days, r, raw, _ = load(args.db)
    print("history ", stats(r))
    rng = np.random.default_rng(1)
    state, z = 0, np.zeros(len(p.center))
    steps = args.years * 252 // PERIOD
    out, states = np.zeros(steps), np.zeros(steps, dtype=int)
    for t in range(steps):
        state, z, out[t] = p.step(state, z, rng)
        states[t] = state
    print("model   ", stats(out))
    runs = {k: [] for k in range(len(STATES))}
    start = 0
    for t in range(1, steps + 1):
        if t == steps or states[t] != states[start]:
            runs[int(states[start])].append((t - start) * PERIOD)
            start = t
    for k, name in enumerate(STATES):
        print(f"{name:10s} time {np.mean(states == k):5.1%}  mean stay {np.mean(runs[k]):5.0f} sessions  median {np.median(runs[k]):5.0f}")
    # 20-year windows: how often a window ends below where it began
    w = 20 * 252 // PERIOD
    ends = np.array([out[i : i + w].sum() for i in range(0, steps - w, w)])
    print("20y windows: median total return", f"{np.exp(np.median(ends)) - 1:.0%}", " 5th pct", f"{np.exp(np.percentile(ends, 5)) - 1:.0%}", " 95th", f"{np.exp(np.percentile(ends, 95)) - 1:.0%}")


if __name__ == "__main__":
    main()
