"""Walk-forward validation of the future model (8g).

    uv run python scripts/walk_forward.py --db PATH [--report scripts/walk_forward_report.md]

At every quarter-end origin (after `MIN_TRAIN_WEEKS` of history) the 8f regime model is
refit on data up to that date only, started from the regime the filter finds at the
origin (what the app does), and simulated forward `PATHS` times. What actually happened
next is then placed inside the simulated range. Nothing here runs in the app; the
database is opened read-only (copy the `daily` and `macro` tables to a scratch file
first for the user's real one).

What it checks, and the pass criteria, fixed here before the first run:

* Calibration of the return, volatility and drawdown over 1 and 3 years: the 80% central
  interval must cover between `COVER80` of the origins, and no more than `OUTER_MAX` of
  the outcomes may fall in the outer 5% on either side (a tail-heavy PIT).
* "Crisis within 3 / 12 months": Brier score against the base rate known at the origin
  (skill >= 0 at 12 months) and a reliability table.
* Macro skill: the model against the same model with the macro inputs removed, and
  against constant-volatility lognormal returns, by CRPS of the 1- and 3-year return
  and the crisis Brier score. Macro inputs "add skill" only if both improve.
* `STICKY` 10 / 20 / 40 / 80: reported; a change is warranted only by a margin of
  `CLEAR_MARGIN` in CRPS and Brier.

Limits: about 25 independent years and 4-5 crises, so this finds gross miscalibration,
not fine tuning; origins a quarter apart overlap for 1-3 year horizons; the realized
"crisis" label is the full-sample fit's smoothed state (it has hindsight, as any label of
what happened does); design choices were made after seeing 2008 and 2020.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import os
import sqlite3
import sys
from dataclasses import replace
from datetime import date
from multiprocessing import Pool
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))  # fit_regime is a sibling script
import fit_regime as fr  # noqa: E402
from paper_trade.market.regime import STATES, RealRegime, RegimeParams  # noqa: E402

PATHS = 500
HORIZONS = (52, 156)  # weeks: 1 and 3 years
CRISIS_HORIZONS = (13, 52)  # weeks: 3 and 12 months
ORIGIN_STEP = 13  # weeks: quarterly
MIN_TRAIN_WEEKS = 520  # 10 years: 1993-2002 is all the history there is before the first origin
CRISIS = len(STATES) - 1
VARIANTS = ("base", "nomacro", "sticky10", "sticky20", "sticky80", "lognormal")
STICKY_OF = {"base": 40.0, "nomacro": 40.0, "sticky10": 10.0, "sticky20": 20.0, "sticky80": 80.0}

# Pass criteria (fixed before running).
COVER80 = (0.65, 0.95)
OUTER_MAX = 0.20
BRIER_SKILL_MIN = 0.0
CLEAR_MARGIN = 0.05

D: dict = {}


def init(db: str) -> None:
    days, r, raw, series = fr.load(db)
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = con.execute("SELECT day, close FROM daily WHERE symbol='SPY' ORDER BY day").fetchall()
    D.update(
        block_starts=days,
        r=r,
        raw=raw,
        series=series,
        days=[date.fromisoformat(d) for d, _ in rows],
        closes=np.array([c for _, c in rows]),
        marks=np.arange(0, len(rows) - fr.PERIOD, fr.PERIOD),
    )


def fit_model(n: int, variant: str) -> tuple[RegimeParams, bool]:
    """The model refit on the first `n` weeks (as `fit_regime.main` does). Returns the
    parameters, the share clipping count and whether the share was clipped."""
    r, raw = D["r"][:n], D["raw"][:n]
    fr.STICKY = STICKY_OF[variant]
    if variant == "nomacro":
        raw = np.zeros_like(raw)
    with contextlib.redirect_stdout(io.StringIO()), np.errstate(all="ignore"):
        params, gamma = fr.fit(r, raw)
        if variant == "nomacro":
            params = replace(params, scale=np.ones_like(params.scale))
        x = np.clip(np.nan_to_num((raw - params.center) / params.scale, nan=0.0), -4, 4)
        level, persistence, noise = fr.feature_dynamics(x, gamma)
        share = gamma.mean(axis=0)
        clipped = bool(share.min() < 1e-3)
        share = np.maximum(share, 1e-3)
        params = RegimeParams(params.mean, params.sigma, params.bias, params.weights, params.center, params.scale,
                              fr.PERIOD, level, persistence, noise, share)
        params = fr.calibrate(params, share, float(r.sum() / (len(r) * fr.PERIOD / 252)))
    return params, clipped


def simulate(p: RegimeParams, state0: int, z0: np.ndarray, steps: int, n: int, rng: np.random.Generator):
    """`n` paths of `steps` periods from the state and features at the origin (the same
    step as `RegimeParams.step`, for all paths at once)."""
    k = len(p.mean)
    state = np.full(n, state0)
    z = np.tile(z0, (n, 1))
    rets = np.empty((n, steps))
    states = np.empty((n, steps), dtype=int)
    rows = np.arange(n)
    for t in range(steps):
        odds = p.transition(z * p.scale + p.center)[rows, state]
        nxt = np.minimum((rng.random(n)[:, None] > np.cumsum(odds, axis=1)).sum(axis=1), k - 1)
        z = np.clip(p.level[nxt] + p.persistence[nxt] * z + p.noise[nxt] * rng.standard_normal(z.shape), -4.0, 4.0)
        rets[:, t] = p.mean[nxt] + p.sigma[nxt] * rng.standard_normal(n)
        states[:, t] = nxt
        state = nxt
    return rets, states


def path_stats(rets: np.ndarray, horizon: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Cumulative log return, annualized volatility and worst drawdown of each path's first `horizon` weeks."""
    x = rets[:, :horizon]
    wealth = np.cumsum(np.concatenate([np.zeros((len(x), 1)), x], axis=1), axis=1)
    drawdown = (1 - np.exp(wealth - np.maximum.accumulate(wealth, axis=1))).max(axis=1)
    return x.sum(axis=1), x.std(axis=1, ddof=1) * np.sqrt(252 / fr.PERIOD), drawdown


def pit(samples: np.ndarray, y: float, rng: np.random.Generator) -> float:
    """Where `y` falls among the samples (0-1; ties placed at random)."""
    lo, hi = float((samples < y).mean()), float((samples <= y).mean())
    return lo + float(rng.random()) * (hi - lo)


def crps(samples: np.ndarray, y: float) -> float:
    x = np.sort(samples)
    m = len(x)
    spread = 2 * float(((2 * np.arange(1, m + 1) - m - 1) * x).sum()) / m**2
    return float(np.abs(x - y).mean() - 0.5 * spread)


def one(task: tuple[int, str]) -> dict:
    n, variant = task
    T = len(D["r"])
    rng = np.random.default_rng([n, 99])
    out: dict = {"n": n, "variant": variant}
    if variant == "lognormal":
        past = D["r"][:n]
        sim = past.mean() + past.std(ddof=1) * rng.standard_normal((PATHS, max(HORIZONS)))
        states = None
    else:
        try:
            params, clipped = fit_model(n, variant)
            m = int(D["marks"][n - 1]) + fr.PERIOD
            found = RealRegime(params, D["days"][: m + 1], D["closes"][: m + 1], D["series"]).at(D["days"][m])
            state0, z0 = found
            sim, states = simulate(params, state0, z0, max(HORIZONS), PATHS, rng)
        except Exception as e:  # a fit that fails is reported, not hidden
            return {**out, "error": f"{type(e).__name__}: {e}"}
        out.update(start=STATES[state0], clipped=clipped)
    for h in HORIZONS:
        if n + h > T:
            continue
        ret, vol, dd = path_stats(sim, h)
        real = path_stats(D["r"][n : n + h][None, :], h)
        out[f"h{h}"] = {
            "pit_ret": pit(ret, float(real[0][0]), rng),
            "pit_vol": pit(vol, float(real[1][0]), rng),
            "pit_dd": pit(dd, float(real[2][0]), rng),
            "crps_ret": crps(ret, float(real[0][0])),
        }
    if states is not None:
        for h in CRISIS_HORIZONS:
            if n + h <= T:
                out[f"crisis{h}"] = float((states[:, :h] == CRISIS).any(axis=1).mean())
    return out


def labels(db: str) -> np.ndarray:
    """Weeks the full-sample fit (smoothed) calls a crisis: what 'happened', for scoring."""
    r, raw = D["r"], D["raw"]
    fr.STICKY = STICKY_OF["base"]
    with contextlib.redirect_stdout(io.StringIO()):
        _, gamma = fr.fit(r, raw)
    return gamma.argmax(axis=1) == CRISIS


def within(c: np.ndarray, h: int) -> np.ndarray:
    """w[t]: a crisis in weeks t .. t+h-1 (NaN where the window runs past the data)."""
    w = np.full(len(c), np.nan)
    for t in range(len(c) - h + 1):
        w[t] = float(c[t : t + h].any())
    return w


def summarize(results: list[dict], crisis_label: np.ndarray) -> dict:
    out: dict = {}
    windows = {h: within(crisis_label, h) for h in CRISIS_HORIZONS}
    for variant in VARIANTS:
        rows = sorted((r for r in results if r["variant"] == variant and "error" not in r), key=lambda r: r["n"])
        s: dict = {"origins": len(rows), "errors": sum(1 for r in results if r["variant"] == variant and "error" in r)}
        for h in HORIZONS:
            have = [r[f"h{h}"] for r in rows if f"h{h}" in r]
            if not have:
                continue
            s[f"h{h}"] = {"n": len(have), "crps_ret": float(np.mean([x["crps_ret"] for x in have]))}
            for name in ("ret", "vol", "dd"):
                u = np.array([x[f"pit_{name}"] for x in have])
                s[f"h{h}"][name] = {
                    "cover50": float(((u >= 0.25) & (u <= 0.75)).mean()),
                    "cover80": float(((u >= 0.10) & (u <= 0.90)).mean()),
                    "outer": float(((u < 0.05) | (u > 0.95)).mean()),
                    "low": float((u < 0.10).mean()),
                    "high": float((u > 0.90).mean()),
                }
        for h in CRISIS_HORIZONS:
            pairs = [(r[f"crisis{h}"], windows[h][r["n"]], float(np.nanmean(windows[h][: r["n"] - h + 1]))) for r in rows if f"crisis{h}" in r]
            if not pairs:
                continue
            p, y, base = (np.array(v) for v in zip(*pairs, strict=True))
            brier, brier_base = float(((p - y) ** 2).mean()), float(((base - y) ** 2).mean())
            bins = [0.0, 0.05, 0.1, 0.2, 0.4, 1.0001]
            table = []
            for lo, hi in zip(bins[:-1], bins[1:], strict=True):
                sel = (p >= lo) & (p < hi)
                if sel.any():
                    table.append({"range": f"{lo:.2f}-{min(hi, 1):.2f}", "n": int(sel.sum()), "predicted": float(p[sel].mean()), "observed": float(y[sel].mean())})
            s[f"crisis{h}"] = {"n": len(p), "brier": brier, "brier_base": brier_base, "skill": 1 - brier / brier_base if brier_base > 0 else float("nan"),
                               "observed_rate": float(y.mean()), "mean_predicted": float(p.mean()), "reliability": table}
        out[variant] = s
    return out


def verdicts(s: dict) -> list[tuple[str, bool, str]]:
    """The fixed pass criteria applied to the base model."""
    base, found = s["base"], []
    for h in HORIZONS:
        b = base.get(f"h{h}")
        if not b:
            continue
        cover = b["ret"]["cover80"]
        found.append((f"{h // 52}y return: 80% interval covers {COVER80[0]:.0%}-{COVER80[1]:.0%}", COVER80[0] <= cover <= COVER80[1], f"{cover:.0%}"))
        for name, label in (("ret", "return"), ("vol", "volatility"), ("dd", "drawdown")):
            found.append((f"{h // 52}y {label}: outer 5% tails hold at most {OUTER_MAX:.0%}", b[name]["outer"] <= OUTER_MAX, f"{b[name]['outer']:.0%}"))
    c = base.get("crisis52")
    if c:
        found.append((f"12-month crisis odds: Brier skill >= {BRIER_SKILL_MIN:g} against the base rate", c["skill"] >= BRIER_SKILL_MIN, f"{c['skill']:+.2f}"))
    return found


def report(s: dict, first: date, last: date, count: int) -> str:
    L = [f"# Walk-forward validation (8g)\n", f"{count} quarter-end origins, {first} to {last}; {PATHS} paths each; training from 1993 (at least {MIN_TRAIN_WEEKS} weeks).\n"]
    L.append("## Pass criteria on the base model (fixed before running)\n")
    L += [f"- {'PASS' if ok else 'FAIL'}: {name} (got {got})" for name, ok, got in verdicts(s)]
    L.append("\n## Calibration (base)\n")
    L.append("| horizon | origins | stat | 50% covered | 80% covered | below 10% | above 90% | outer 5% |")
    L.append("|---|---|---|---|---|---|---|---|")
    for h in HORIZONS:
        b = s["base"].get(f"h{h}")
        for name in ("ret", "vol", "dd"):
            if b:
                x = b[name]
                L.append(f"| {h // 52}y | {b['n']} | {name} | {x['cover50']:.0%} | {x['cover80']:.0%} | {x['low']:.0%} | {x['high']:.0%} | {x['outer']:.0%} |")
    L.append("\nWell calibrated: 50% / 80% covered, 10% below and above, 10% in the outer 5%. Too many outside both ends = too confident; many on one end = biased.\n")
    L.append("## Crisis within 3 / 12 months (base)\n")
    for h in CRISIS_HORIZONS:
        c = s["base"].get(f"crisis{h}")
        if c:
            L.append(f"**{h // 4 if h < 52 else 12} months**: Brier {c['brier']:.4f} against {c['brier_base']:.4f} for the base rate known at the origin (skill {c['skill']:+.2f}); mean predicted {c['mean_predicted']:.1%}, observed {c['observed_rate']:.1%}.\n")
            L.append("| predicted | origins | mean predicted | observed |\n|---|---|---|---|")
            L += [f"| {t['range']} | {t['n']} | {t['predicted']:.1%} | {t['observed']:.1%} |" for t in c["reliability"]]
            L.append("")
    L.append("## Macro inputs and the constant-volatility baseline (lower CRPS / Brier is better)\n")
    L.append("| model | 1y CRPS | 3y CRPS | 3m Brier | 12m Brier |\n|---|---|---|---|---|")
    for v in ("base", "nomacro", "lognormal"):
        x = s[v]
        cells = [f"{x[k]['crps_ret']:.4f}" if k in x else "-" for k in ("h52", "h156")] + [f"{x[k]['brier']:.4f}" if k in x else "-" for k in ("crisis13", "crisis52")]
        L.append(f"| {v} | " + " | ".join(cells) + " |")
    b, nm = s["base"], s["nomacro"]
    better = all(b[k]["crps_ret"] < nm[k]["crps_ret"] for k in ("h52", "h156") if k in b and k in nm) and all(
        b[k]["brier"] < nm[k]["brier"] for k in ("crisis13", "crisis52") if k in b and k in nm)
    L.append(f"\nMacro inputs {'improve' if better else 'do NOT improve'} both the return CRPS and the crisis Brier score over the no-macro model.\n")
    L.append("## STICKY\n")
    L.append("| STICKY | 1y CRPS | 3y CRPS | 3m Brier | 12m Brier | 1y 80% cover |\n|---|---|---|---|---|---|")
    for v in ("sticky10", "sticky20", "base", "sticky80"):
        x = s[v]
        cells = [f"{x[k]['crps_ret']:.4f}" if k in x else "-" for k in ("h52", "h156")] + [f"{x[k]['brier']:.4f}" if k in x else "-" for k in ("crisis13", "crisis52")]
        L.append(f"| {STICKY_OF[v]:g}{' (current)' if v == 'base' else ''} | " + " | ".join(cells) + f" | {x['h52']['ret']['cover80']:.0%} |")
    L.append(f"\nA different STICKY would be warranted only by a margin of {CLEAR_MARGIN:.0%} in both CRPS and Brier.\n")
    L.append("## Reading this\n")
    L.append("- This tests the regime layer (weekly returns by state), not the whole engine: the engine adds fat-tailed gaps, volatility clustering inside a state and per-symbol noise, so its volatility and drawdown ranges are wider than these.")
    L.append("- A 'crisis' here is a week the full-sample fit's smoothed path calls crisis (a narrow label, 38 weeks since 1993). A model refit on early data may call a milder bear (2002) a crisis, so some of the crisis-odds miss is a label mismatch, not only model error.")
    L.append("- Origins a quarter apart overlap for 1 and 3 years, so the effective sample is about 25 years and 4-5 crises: gross miscalibration shows, fine tuning does not.\n")
    errors = {v: s[v]["errors"] for v in VARIANTS if s[v]["errors"]}
    if errors:
        L.append(f"Fits that failed (skipped): {errors}\n")
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--report", default=str(Path(__file__).with_name("walk_forward_report.md")))
    ap.add_argument("--workers", type=int, default=min(8, os.cpu_count() or 1))
    ap.add_argument("--limit", type=int, default=0, help="only the first N origins (a quick trial)")
    args = ap.parse_args()
    init(args.db)
    T = len(D["r"])
    origins = list(range(MIN_TRAIN_WEEKS, T - min(HORIZONS) + 1, ORIGIN_STEP))
    if args.limit:
        origins = origins[: args.limit]
    ends = [D["days"][int(D["marks"][n - 1]) + fr.PERIOD] for n in origins]
    print(f"{len(origins)} origins {ends[0]} to {ends[-1]}, {len(VARIANTS)} models each", flush=True)
    crisis_label = labels(args.db)
    print(f"full-sample crisis weeks: {int(crisis_label.sum())} of {T}", flush=True)
    tasks = [(n, v) for n in origins for v in VARIANTS]
    results = []
    with Pool(args.workers, initializer=init, initargs=(args.db,)) as pool:
        for i, res in enumerate(pool.imap_unordered(one, tasks), 1):
            results.append(res)
            if i % 50 == 0:
                print(f"{i}/{len(tasks)}", flush=True)
    summary = summarize(results, crisis_label)
    text = report(summary, ends[0], ends[-1], len(origins))
    Path(args.report).write_text(text)
    print(text)


if __name__ == "__main__":
    main()
