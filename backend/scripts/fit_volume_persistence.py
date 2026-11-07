"""Fit each symbol's day-level volume persistence: `volume_persistence.json`.

    uv run python scripts/fit_volume_persistence.py --db PATH [--iterations 3] [--seeds 8] [--out PATH]

Real daily volume stays high or low for different lengths of time by symbol (TSLA's lag-20
autocorrelation of detrended log volume is .56, KO's .11). The engine's day-level volume
is a market part and a symbol's own part, each a fast AR(1) plus a slow one (plus a white
rest). Per symbol this fits the size of both parts, the shares of the own part and the slow
rate, so the simulated autocovariance of detrended log volume (a 250-day rolling median
removed, as in `check_volume.py`) matches the real one at lags 1 to 40.

The engine's other volume terms (the volatility regime's level, the order-flow imbalance, heavy
days, events) also persist or not, so a single fit from theory would miss: the fit is iterated
against the engine itself. Each round simulates `--seeds` futures from the real start, takes
what the simulation has beyond the activity (its autocovariance minus the activity's own) and
fits the activity to what is missing from the real series. The volatility link
(`VOLUME_VOL_LINK`, market and own sizes in the engine) is not touched, so prices stay as
they were. `--db` is a scratch copy of the `daily` and `macro` tables, never written.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import date
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import check_volume as cv  # noqa: E402
import walk_forward_engine as we  # noqa: E402
from paper_trade.market import calendar as cal  # noqa: E402
from paper_trade.market import engine as engine_mod  # noqa: E402
from paper_trade.market.engine import VOLUME_FAST, VOLUME_FLOW_SIZE, VOLUME_MARKET_SD, VOLUME_OWN_SD, VOLUME_SLOW, MarketEngine  # noqa: E402
from paper_trade.market.realbars import RealBars  # noqa: E402

NAMES = cv.NAMES
LAGS = np.array([1, 2, 3, 5, 10, 20, 40])
SLOW_RATES = (0.90, 0.94, 0.97, 0.985, 0.995)
DAMP = 0.7  # how far a round moves toward its fit
TRIM = 125
SIM_DAYS = 800


def detrend(volume: np.ndarray) -> np.ndarray:
    lv = np.log(volume[1:])
    return np.column_stack([lv[:, j] - cv.rolling_median(lv[:, j]) for j in range(lv.shape[1])])[TRIM:-TRIM]


def autocov(d: np.ndarray) -> np.ndarray:
    """(symbols, 1 + len(LAGS)): the variance, then the autocovariance at each lag."""
    d = d - d.mean(axis=0)
    out = np.empty((d.shape[1], 1 + len(LAGS)))
    out[:, 0] = (d * d).mean(axis=0)
    for k, lag in enumerate(LAGS):
        out[:, 1 + k] = (d[:-lag] * d[lag:]).mean(axis=0)
    return out


def real_series(db: str) -> np.ndarray:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    cols = {s: {date.fromisoformat(r[0]): r[1] for r in con.execute("SELECT day, volume FROM daily WHERE symbol=? AND day>='2021-01-04' AND volume>0 ORDER BY day", (s,))} for s in NAMES}
    days = sorted(set.intersection(*[set(c) for c in cols.values()]))
    return detrend(np.array([[cols[s][d] for s in NAMES] for d in days], float))


def simulate(full: RealBars, seeds: int) -> np.ndarray:
    """(seeds, days, symbols) detrended simulated log volume from the real start."""
    start = cal.next_trading_day(full.last_day)
    out = []
    for seed in range(seeds):
        engine = MarketEngine(cal.session_open(start), seed=seed, real=full)
        vol: list[np.ndarray] = []
        engine.on_day_complete = lambda day, engine=engine, vol=vol: vol.append(engine._skeleton(day).volume.sum(axis=0))
        engine.advance(cal.session_open(cal.add_trading_days(start, SIM_DAYS)))
        out.append(detrend(np.array(vol)))
    return np.array(out)


def nnls(a: np.ndarray, b: np.ndarray) -> tuple[np.ndarray, float]:
    """Non-negative least squares for two columns (no scipy here): the unconstrained fit, else the best single column."""
    w = np.linalg.lstsq(a, b, rcond=None)[0]
    if (w >= 0).all():
        return w, float(np.sum((a @ w - b) ** 2))
    best = (np.zeros(2), float(np.sum(b**2)))
    for j in range(2):
        col = a[:, j]
        wj = max(float(col @ b) / float(col @ col), 0.0)
        err = float(np.sum((wj * col - b) ** 2))
        if err < best[1]:
            best = (np.eye(2)[j] * wj, err)
    return best


def activity_cov(market_sd, own_sd, fast, slow, slow_rho) -> np.ndarray:
    """(symbols, len(LAGS)): the autocovariance of the day-level activity the engine builds from these parameters."""
    k = LAGS[None, :]
    market = (VOLUME_FAST[1] * VOLUME_FAST[0] ** k + VOLUME_SLOW[1] * VOLUME_SLOW[0] ** k)
    own = fast[:, None] * VOLUME_FAST[0] ** k + slow[:, None] * slow_rho[:, None] ** k
    return market_sd[:, None] ** 2 * market + own_sd[:, None] ** 2 * own


def fit_round(real: np.ndarray, sims: np.ndarray, cur: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    c_real = autocov(real)
    c_sim = np.mean([autocov(s) for s in sims], axis=0)
    m_real = real.mean(axis=1)
    share_real = np.array([np.corrcoef(real[:, j], m_real)[0, 1] ** 2 for j in range(real.shape[1])])
    share_sim = np.mean([[np.corrcoef(s[:, j], s.mean(axis=1))[0, 1] ** 2 for j in range(s.shape[1])] for s in sims], axis=0)
    own_activity = activity_cov(cur["market_sd"], cur["own_sd"], cur["fast_share"], cur["slow_share"], cur["slow_rho"])
    other = c_sim[:, 1:] - own_activity  # what the simulation has besides the activity
    target = np.maximum(c_real[:, 1:] - other, 0.0)
    market_sd = np.clip(cur["market_sd"] * np.sqrt(np.clip(share_real / share_sim, 0.4, 2.5)), 0.03, 0.25)
    market_part = activity_cov(market_sd, np.zeros(len(NAMES)), cur["fast_share"], cur["slow_share"], cur["slow_rho"])
    new = {k: v.copy() for k, v in cur.items()}
    new["market_sd"] = market_sd
    other0 = c_sim[:, 0] - (cur["market_sd"] ** 2 + cur["own_sd"] ** 2)  # the simulation's variance besides the activity (unit-variance parts)
    for i in range(len(NAMES)):
        want = np.maximum(target[i] - market_part[i], 0.0)
        own_var = max(c_real[i, 0] - other0[i] - market_sd[i] ** 2, 0.004)  # the variance the symbol's own activity has to supply
        best = None
        for rho in SLOW_RATES:
            basis = np.column_stack([VOLUME_FAST[0] ** LAGS, rho**LAGS])
            w, err = nnls(basis, want)
            if best is None or err < best[0]:
                best = (err, rho, w)
        _, rho, (wf, ws) = best
        # A symbol whose volume is stickier than the variance left over allows gives up some of the white
        # order-flow part (its size is a white term worth size^2 * (1 - 2/pi) of variance).
        deficit = max(wf + ws - own_var, 0.0)
        flow_var = cur["flow_size"][i] ** 2 * (1 - 2 / np.pi)
        flow_size = float(np.sqrt(max(flow_var - deficit, 0.0) / (1 - 2 / np.pi))) if deficit else cur["flow_size"][i]
        flow_size = max(flow_size, 0.08)
        own_var = own_var + (flow_var - flow_size**2 * (1 - 2 / np.pi))  # what it freed
        scale = min(1.0, own_var / max(wf + ws, 1e-9))  # the persistent parts cannot exceed the variance: the rest is white
        fit = {"own_sd": min(np.sqrt(own_var), 0.6), "fast_share": wf * scale / own_var, "slow_share": ws * scale / own_var, "slow_rho": rho, "flow_size": flow_size}
        for k, v in fit.items():
            new[k][i] = DAMP * v + (1 - DAMP) * cur[k][i]
    return new


def write(path: Path, params: dict[str, np.ndarray]) -> None:
    out = {"symbols": {n: {k: round(float(params[k][i]), 5) for k in params} for i, n in enumerate(NAMES)}}
    path.write_text(json.dumps(out, indent=1) + "\n")


def report(real: np.ndarray, sims: np.ndarray) -> float:
    c_real, c_sim = autocov(real), np.mean([autocov(s) for s in sims], axis=0)
    ac = lambda c: c[:, 1:] / c[:, :1]  # noqa: E731
    r, m = ac(c_real), ac(c_sim)
    print(f"{'':8s} sd real/sim   " + "  ".join(f"lag{lag:>3d} r/s" for lag in LAGS))
    for name in ("SPY", "AAPL", "KO", "TSLA"):
        i = NAMES.index(name)
        print(f"{name:8s} {np.sqrt(c_real[i, 0]):.2f}/{np.sqrt(c_sim[i, 0]):.2f}    " + "  ".join(f"{r[i, k]:5.2f}/{m[i, k]:4.2f}" for k in range(len(LAGS))))
    print(f"{'median':8s} {np.median(np.sqrt(c_real[:, 0])):.2f}/{np.median(np.sqrt(c_sim[:, 0])):.2f}    " + "  ".join(f"{np.median(r[:, k]):5.2f}/{np.median(m[:, k]):4.2f}" for k in range(len(LAGS))))
    err = np.abs(r - m)[:, :].mean(axis=0)
    print("mean absolute autocorrelation error over symbols, by lag: " + " ".join(f"{e:.3f}" for e in err))
    return float(err.mean())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--iterations", type=int, default=3)
    ap.add_argument("--seeds", type=int, default=8)
    ap.add_argument("--out", type=Path, default=engine_mod.PERSISTENCE_FILE)
    a = ap.parse_args()
    work = a.out.with_name(a.out.name + ".fit")  # rounds write here; the real file is replaced only by a fit that ended better
    engine_mod.PERSISTENCE_FILE = work  # the engine reads the file being fitted
    we.init(a.db)
    full = RealBars(NAMES, we.G["bars"], we.G["vix"], we.G["series"])
    real = real_series(a.db)
    n = len(NAMES)
    cur = {
        "market_sd": np.full(n, VOLUME_MARKET_SD), "own_sd": np.full(n, VOLUME_OWN_SD), "fast_share": np.full(n, VOLUME_FAST[1]),
        "slow_share": np.full(n, VOLUME_SLOW[1]), "slow_rho": np.full(n, VOLUME_SLOW[0]), "flow_size": np.full(n, VOLUME_FLOW_SIZE),
    }  # fmt: skip
    errors = []
    for it in range(a.iterations + 1):
        write(work, cur)  # the engine reads the file when it is built
        sims = simulate(full, a.seeds)
        print(f"\nround {it} (params in {work.name})")
        errors.append(report(real, sims))
        if it < a.iterations:
            cur = fit_round(real, sims, cur)
    if errors[-1] < errors[0]:
        os.replace(work, a.out)
        print(f"\nfit better than the start ({errors[0]:.3f} -> {errors[-1]:.3f}); written to {a.out}")
    else:
        work.unlink()
        print(f"\nno better than the start ({errors[0]:.3f} -> {errors[-1]:.3f}); {a.out} left as it was")

if __name__ == "__main__":
    main()
