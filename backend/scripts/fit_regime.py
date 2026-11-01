"""Fit the market regime model (8f) offline.

    uv run python scripts/fit_regime.py --db PATH [--out src/paper_trade/market/regime_params.json]

Reads SPY's daily bars and the FRED series from a database (open read-only; copy
the `daily` and `macro` tables to a scratch file first for the user's real one),
fits a 3-state hidden Markov model with macro-driven transitions by EM, and saves
the parameters. Nothing here runs in the app.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import date
from pathlib import Path

import numpy as np

from paper_trade.market.regime import FEATURES, PARAMS_FILE, STATES, RegimeParams, feature_panel, stationary

K, F = len(STATES), len(FEATURES)
L2 = 1.0
PERIOD = 5  # sessions per step: weekly returns, so regimes last months, not days
# Persistence prior: this many pseudo-days of staying put per state (daily returns alone
# would otherwise flip states every few days, fitting noise instead of regimes).
STICKY = float(__import__('os').environ.get('STICKY', 40))


def load(db: str) -> tuple[list[date], np.ndarray, np.ndarray, dict]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = con.execute("SELECT day, close FROM daily WHERE symbol='SPY' ORDER BY day").fetchall()
    series: dict[str, list[tuple[date, float]]] = {}
    for name, day, value in con.execute("SELECT series, day, value FROM macro ORDER BY series, day"):
        series.setdefault(name, []).append((date.fromisoformat(day), value))
    days = [date.fromisoformat(d) for d, _ in rows]
    close = np.array([c for _, c in rows])
    # Non-overlapping blocks of PERIOD sessions: a block's return, and the macro picture
    # known before its first session.
    marks = np.arange(0, len(close) - PERIOD, PERIOD)
    r = np.log(close[marks + PERIOD] / close[marks])
    starts = [days[m + 1] for m in marks]
    return starts, r, feature_panel(starts, series), series


def forward_backward(r, mean, sigma, P):
    """P[t] is the (K, K) transition into day t from day t-1 (P[0] unused)."""
    T = len(r)
    logb = -0.5 * ((r[:, None] - mean) / sigma) ** 2 - np.log(sigma) - 0.5 * np.log(2 * np.pi)
    b = np.exp(logb - logb.max(axis=1, keepdims=True))
    scale = logb.max(axis=1)
    alpha = np.zeros((T, K))
    c = np.zeros(T)
    a = np.full(K, 1.0 / K) * b[0]
    c[0] = a.sum()
    alpha[0] = a / c[0]
    for t in range(1, T):
        a = (alpha[t - 1] @ P[t]) * b[t]
        c[t] = a.sum()
        alpha[t] = a / c[t]
    beta = np.ones((T, K))
    for t in range(T - 2, -1, -1):
        beta[t] = (P[t + 1] @ (b[t + 1] * beta[t + 1])) / c[t + 1]
    gamma = alpha * beta
    gamma /= gamma.sum(axis=1, keepdims=True)
    xi = alpha[:-1, :, None] * P[1:] * (b[1:] * beta[1:])[:, None, :] / c[1:, None, None]
    return gamma, xi, float(np.sum(np.log(c) + scale))


def softmax(z):
    z = z - z.max(axis=-1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=-1, keepdims=True)


def fit_transitions(X, xi, bias, weights, steps=60):
    """Weighted multinomial logistic regression for each origin state (gradient ascent)."""
    Xp = X[1:]  # features known before day t+1's move (feature_panel already uses the day before)
    for i in range(K):
        prior = np.full(K, 1.0)
        prior[i] = STICKY
        target = np.vstack([xi[:, i, :], prior])  # (T-1, K) expected transitions from i, plus the prior
        Xi = np.vstack([Xp, np.zeros(F)])
        n = target.sum()
        if n < 1:
            continue
        for _ in range(steps):
            p = softmax(bias[i] + Xi @ weights[i].T)
            err = target - target.sum(axis=1, keepdims=True) * p  # (T-1, K)
            gb = err.sum(axis=0) / n
            gw = (err.T @ Xi) / n - L2 * weights[i] / n
            bias[i] += 2.0 * gb
            weights[i] += 2.0 * gw
    return bias, weights


def fit(r, raw, iterations=150):
    center = np.nanmean(raw, axis=0)
    scale = np.nanstd(raw, axis=0)
    X = np.clip(np.nan_to_num((raw - center) / scale, nan=0.0), -4, 4)
    vol = np.array([np.abs(r[max(0, t - 4) : t + 1]).mean() for t in range(len(r))])
    q = np.quantile(vol, [1 / 3 * 1.5 / 1.5, 0.85])
    init = np.digitize(vol, [np.quantile(vol, 0.6), np.quantile(vol, 0.92)])
    mean = np.array([r[init == k].mean() for k in range(K)])
    sigma = np.array([r[init == k].std() for k in range(K)])
    bias = np.log(np.where(np.eye(K) > 0, 0.97, 0.015))
    weights = np.zeros((K, K, F))
    last = -np.inf
    for it in range(iterations):
        logits = bias[None] + np.einsum("ijf,tf->tij", weights, np.vstack([np.zeros(F), X[1:]]))
        P = softmax(logits)
        gamma, xi, ll = forward_backward(r, mean, sigma, P)
        w = gamma.sum(axis=0)
        mean = (gamma * r[:, None]).sum(axis=0) / w
        sigma = np.sqrt((gamma * (r[:, None] - mean) ** 2).sum(axis=0) / w)
        order = np.argsort(sigma)
        if not np.array_equal(order, np.arange(K)):
            mean, sigma, gamma = mean[order], sigma[order], gamma[:, order]
            bias = bias[np.ix_(order, order)]
            weights = weights[np.ix_(order, order)]
            xi = xi[:, order][:, :, order]
        bias, weights = fit_transitions(X, xi, bias, weights)
        if it % 25 == 0:
            print(f"iter {it:3d} loglik {ll:.1f}")
        if abs(ll - last) < 1e-3:
            break
        last = ll
    print(f"final loglik {ll:.1f}")
    return RegimeParams(mean, sigma, bias, weights, center, scale), gamma


def feature_dynamics(X, gamma):
    """Per-state AR(1) for each standardized feature, weighted by the state's posterior."""
    level, phi, noise = np.zeros((K, F)), np.zeros((K, F)), np.zeros((K, F))
    for k in range(K):
        w = gamma[1:, k]
        for f in range(F):
            x0, x1 = X[:-1, f], X[1:, f]
            m0, m1 = np.average(x0, weights=w), np.average(x1, weights=w)
            var = np.average((x0 - m0) ** 2, weights=w)
            b = np.average((x0 - m0) * (x1 - m1), weights=w) / var if var > 1e-9 else 0.0
            b = float(np.clip(b, 0.0, 0.98))
            level[k, f] = m1 - b * m0
            phi[k, f] = b
            noise[k, f] = np.sqrt(np.average((x1 - level[k, f] - b * x0) ** 2, weights=w))
    return level, phi, noise


CRISIS_DRIFT_FLOOR = -0.45  # per year: a crisis falls hard, but it is not a one-way street


def simulate_states(p, steps, seed=7):
    rng = np.random.default_rng(seed)
    state, z = 0, np.zeros(F)
    states = np.zeros(steps, dtype=int)
    rets = np.zeros(steps)
    for t in range(steps):
        state, z, rets[t] = p.step(state, z, rng)
        states[t] = state
    return states, rets


def calibrate(p, share, yearly_log_return, years=300):
    """Make the simulated model match history: the share of time in each state (by
    nudging the logits into it), the drift floor for a crisis and the long-run return."""
    bias = p.bias.copy()
    steps = years * 252 // PERIOD
    q = p
    for it in range(10):
        q = RegimeParams(p.mean, p.sigma, bias, p.weights, p.center, p.scale, p.period, p.level, p.persistence, p.noise, p.share)
        states, _ = simulate_states(q, steps)
        got = np.array([(states == k).mean() for k in range(K)]).clip(1e-3)
        bias = bias + 0.6 * np.log(share / got)[None, :]
    per = PERIOD / 252
    mean = p.mean.copy()
    mean[2] = max(mean[2], CRISIS_DRIFT_FLOOR * per)
    states, _ = simulate_states(q, steps)
    freq = np.array([(states == k).mean() for k in range(K)])
    mean = mean + (yearly_log_return * per - float(freq @ mean))  # one shift keeps the gaps between states
    return RegimeParams(mean, p.sigma, bias, p.weights, p.center, p.scale, p.period, p.level, p.persistence, p.noise, p.share)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", default=str(PARAMS_FILE))
    args = ap.parse_args()
    days, r, raw, _ = load(args.db)
    print(f"{len(r)} SPY days {days[0]} to {days[-1]}")
    params, gamma = fit(r, raw)
    X = np.clip(np.nan_to_num((raw - params.center) / params.scale, nan=0.0), -4, 4)
    level, persistence, noise = feature_dynamics(X, gamma)
    params = RegimeParams(params.mean, params.sigma, params.bias, params.weights, params.center, params.scale, PERIOD, level, persistence, noise, gamma.mean(axis=0))
    params = calibrate(params, gamma.mean(axis=0), float(r.sum() / (len(r) * PERIOD / 252)))
    avg = params.transition(raw).mean(axis=0)  # the fitted odds averaged over history
    ann = np.sqrt(252 / PERIOD)
    per_year = 252 / PERIOD
    for k, name in enumerate(STATES):
        stay = avg[k, k]
        print(f"{name:10s} drift {params.mean[k]*per_year:+7.1%}/yr  vol {params.sigma[k]*ann:5.1%}/yr  "
              f"time {gamma[:, k].mean():5.1%}  mean stay {PERIOD/(1-stay):6.0f} days  stationary {stationary(avg)[k]:5.1%}")
    state = gamma.argmax(axis=1)
    for label, a, b in [("2008 GFC", date(2008, 9, 1), date(2009, 3, 31)), ("2020 crash", date(2020, 2, 15), date(2020, 4, 15)),
                        ("2017 calm", date(2017, 1, 1), date(2017, 12, 31)), ("2022 bear", date(2022, 1, 1), date(2022, 10, 31)),
                        ("2000-02", date(2000, 4, 1), date(2002, 10, 1)), ("2025 Apr", date(2025, 3, 15), date(2025, 5, 15))]:
        m = [i for i, d in enumerate(days) if a <= d <= b]
        if m:
            print(f"{label:10s}", {STATES[k]: round(float((state[m] == k).mean()), 2) for k in range(K)})
    Path(args.out).write_text(json.dumps(params.to_json(), indent=1))
    print("feature slopes into crisis from calm:", dict(zip(FEATURES, np.round(params.weights[0, 2], 2), strict=True)))
    print("saved", args.out)


if __name__ == "__main__":
    main()
