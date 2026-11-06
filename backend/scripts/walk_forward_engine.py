"""Engine-level walk-forward spot checks (8g, second part).

    uv run python scripts/walk_forward_engine.py --db PATH [--report scripts/walk_forward_engine_report.md]

`walk_forward.py` tests the regime layer alone. Here the real engine runs: at each of
`ORIGINS` the regime model is refit on data before the origin (as there), the engine
is started as a new market at that date with only the real bars before it (so its
start state is the real VIX and the filtered regime, as in the app), and `PATHS`
seeds are run forward 3 years. SPY's simulated weekly returns are placed inside the
real outcome exactly as in `walk_forward.py`, next to the regime layer's own range
from the same refit and start state. Reads the database read-only in spirit: copy the
`daily` and `macro` tables to a scratch file first (it is never written to).

Questions, with the criteria fixed before the first run:

* Does the engine (clustering, fat tails, crashes on top of the regime layer) make the
  volatility and drawdown ranges wider than the regime layer's, and closer to calibrated?
  (reported per origin and pooled; 8 origins can only show a gross difference)
* `STATE_LEVEL_ADJ`: the engine's weekly volatility in each state against what the
  history shows in that state (the smoothed labels of the full-sample fit); it passes
  within `STATE_VOL_TOL` of history in every state.
* `CRASH_BY_STATE`: the engine's frequency of daily SPY moves of -3% or worse (and -5%)
  against history overall and by state; it passes within a factor of `CRASH_FACTOR`.
* `SHOCK_IN_CRISIS` is not checked: it only acts when the user triggers a shock, and
  history has no triggers to compare with.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date
from multiprocessing import Pool
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import fit_regime as fr  # noqa: E402
import walk_forward as wf  # noqa: E402
from paper_trade.macro import SERIES  # noqa: E402
from paper_trade.market import calendar  # noqa: E402
from paper_trade.market import engine as engine_mod  # noqa: E402
from paper_trade.market import realbars as realbars_mod  # noqa: E402
from paper_trade.market.engine import MarketEngine  # noqa: E402
from paper_trade.market.realbars import RealBars  # noqa: E402
from paper_trade.market.regime import STATES, RealRegime  # noqa: E402
from paper_trade.market.symbols import SYMBOLS  # noqa: E402
from paper_trade.pricehistory import Bar  # noqa: E402

PATHS = 160
CHUNK = 20  # seeds a task runs
WEEKS = max(wf.HORIZONS)
SESSIONS = WEEKS * fr.PERIOD
# Origins: ahead of calm, ahead of the 2008 and 2020 crises, mid-recovery, and recent.
ORIGINS = (date(2004, 6, 30), date(2007, 6, 29), date(2009, 9, 30), date(2012, 12, 31),
           date(2016, 6, 30), date(2019, 6, 28), date(2021, 12, 31), date(2023, 6, 30))
STATE_VOL_TOL = 0.15  # engine weekly volatility per state within this of history
CRASH_FACTOR = 1.5  # engine frequency of -3% / -5% days within this factor of history
CRASH_LEVELS = (-0.03, -0.05)

G: dict = {}


def load_all(db: str) -> tuple[dict, dict, list]:
    """Real bars of every symbol, the macro series and the VIX, from the database."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    bars: dict[str, list[Bar]] = {s.symbol: [] for s in SYMBOLS}
    for row in con.execute("SELECT symbol, day, open, high, low, close, volume, dividend, split FROM daily ORDER BY symbol, day"):
        if row[0] in bars:
            bars[row[0]].append(Bar(date.fromisoformat(row[1]), *row[2:]))
    series: dict[str, list[tuple[date, float]]] = {name: [] for name in SERIES}
    for name, day, value in con.execute("SELECT series, day, value FROM macro ORDER BY series, day"):
        if name in series:
            series[name].append((date.fromisoformat(day), value))
    con.close()
    return bars, series, series["VIXCLS"]


def init(db: str) -> None:
    wf.init(db)
    bars, series, vix = load_all(db)
    full = RealBars([s.symbol for s in SYMBOLS], bars, vix, series)
    calendar.set_overrides(*full.closures())  # the real closures, so sessions line up with the real weeks
    G.update(bars=bars, series=series, vix=vix, cache={}, spy=[s.symbol for s in SYMBOLS].index("SPY"))


def truncated(k: int, cutoff: date) -> RealBars:
    """The real bars through `cutoff` only (what a market begun then would have)."""
    cache = G["cache"]
    if k not in cache:
        cache.clear()  # one origin at a time per worker: memory is the limit, not time
        names = [s.symbol for s in SYMBOLS]
        bars = {s: [b for b in G["bars"][s] if b.day <= cutoff] for s in names}
        series = {n: [(d, v) for d, v in rows if d <= cutoff] for n, rows in G["series"].items()}
        cache[k] = RealBars(names, bars, series["VIXCLS"], series)
    return cache[k]


def run(task: tuple[int, date, object, list[int]]) -> dict:
    """Engine runs from the origin `cutoff` with the refit `params`: weekly SPY returns, daily
    returns and the regime state of each session, for each seed."""
    k, cutoff, params, seeds = task
    engine_mod.load_params = lambda: params  # the refit model, not the committed one
    realbars_mod.load_params = lambda: params
    real = truncated(k, cutoff)
    start_day = calendar.next_trading_day(cutoff)
    days = [start_day]
    for _ in range(SESSIONS):
        days.append(calendar.next_trading_day(days[-1]))
    weekly, daily, states = [], [], []
    for seed in seeds:
        engine = MarketEngine(calendar.session_open(start_day), seed=seed, real=real)
        assert engine._macro is params, "the engine must run on the refit model, not the committed one"
        base = float(np.log(engine._starts[start_day].prev_close[G["spy"]]))
        closes: list[float] = []
        state: list[int] = []

        def done(day: date) -> None:
            sk = engine._skeleton(day)
            closes.append(float(sk.close[-1, G["spy"]]))
            state.append(sk.state)

        engine.on_day_complete = done
        engine.advance(calendar.session_open(days[-1]))
        log = np.concatenate([[base], closes[:SESSIONS]])
        d = np.diff(log)
        weekly.append(d.reshape(WEEKS, fr.PERIOD).sum(axis=1))
        daily.append(d)
        states.append(np.array(state[:SESSIONS]))
        del engine
    return {"k": k, "weekly": np.array(weekly), "daily": np.array(daily), "states": np.array(states)}


def real_states() -> np.ndarray:
    """Each week's state in the full-sample fit's smoothed path (what history was)."""
    fr.STICKY = wf.STICKY_OF["base"]
    import contextlib
    import io

    with contextlib.redirect_stdout(io.StringIO()):
        _, gamma = fr.fit(wf.D["r"], wf.D["raw"])
    return gamma.argmax(axis=1)


def pct(x: np.ndarray, q: float) -> float:
    return float(np.percentile(x, q))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--report", default=str(Path(__file__).with_name("walk_forward_engine_report.md")))
    ap.add_argument("--workers", type=int, default=min(4, __import__("os").cpu_count() or 1))
    ap.add_argument("--cache", help="save the engine runs here (npz), or read them from here if it exists")
    ap.add_argument("--limit", type=int, default=0, help="only the first N origins (a quick trial)")
    args = ap.parse_args()
    init(args.db)
    D, T = wf.D, len(wf.D["r"])
    origins = ORIGINS[: args.limit] if args.limit else ORIGINS
    # The block count n whose last week ends nearest each origin date.
    ends = [D["days"][int(D["marks"][n - 1]) + fr.PERIOD] for n in range(1, T + 1)]
    ns = [min(range(wf.MIN_TRAIN_WEEKS, T - WEEKS + 1), key=lambda n, o=o: abs((ends[n - 1] - o).days)) for o in origins]
    cutoffs = [ends[n - 1] for n in ns]
    print("origins:", [str(c) for c in cutoffs], flush=True)

    fits, starts = [], []
    for n, cutoff in zip(ns, cutoffs, strict=True):
        params, _ = wf.fit_model(n, "base")
        m = int(D["marks"][n - 1]) + fr.PERIOD
        state0, z0 = RealRegime(params, D["days"][: m + 1], D["closes"][: m + 1], D["series"]).at(D["days"][m])
        fits.append(params)
        starts.append((state0, z0))
    tasks = [(k, cutoffs[k], fits[k], list(range(i, min(i + CHUNK, PATHS)))) for k in range(len(ns)) for i in range(0, PATHS, CHUNK)]
    done: dict[int, list[dict]] = {k: [] for k in range(len(ns))}
    if args.cache and Path(args.cache).exists():
        saved = np.load(args.cache)
        for k in range(len(ns)):
            done[k] = [{"weekly": saved[f"weekly{k}"], "daily": saved[f"daily{k}"], "states": saved[f"states{k}"]}]
    else:
        with Pool(args.workers, initializer=init, initargs=(args.db,), maxtasksperchild=4) as pool:
            for i, res in enumerate(pool.imap_unordered(run, tasks), 1):
                done[res["k"]].append(res)
                print(f"{i}/{len(tasks)}", flush=True)
        if args.cache:
            np.savez_compressed(args.cache, **{f"{name}{k}": np.vstack([r[name] for r in done[k]]) for k in range(len(ns)) for name in ("weekly", "daily", "states")})

    rng = np.random.default_rng(3)
    rows, pooled = [], {h: {"engine": {x: [] for x in ("ret", "vol", "dd")}, "layer": {x: [] for x in ("ret", "vol", "dd")}} for h in wf.HORIZONS}
    sim_weekly, sim_daily, sim_state = [], [], []
    for k, n in enumerate(ns):
        weekly = np.vstack([r["weekly"] for r in done[k]])
        sim_weekly.append(weekly)
        sim_daily.append(np.vstack([r["daily"] for r in done[k]]))
        sim_state.append(np.vstack([r["states"] for r in done[k]]))
        layer, _ = wf.simulate(fits[k], starts[k][0], starts[k][1], WEEKS, wf.PATHS, np.random.default_rng([n, 5]))
        for h in wf.HORIZONS:
            real = wf.path_stats(D["r"][n : n + h][None, :], h)
            for name, sim in (("engine", weekly), ("layer", layer)):
                stats = wf.path_stats(sim, h)
                row = {"origin": str(cutoffs[k]), "h": h, "who": name, "start": STATES[starts[k][0]]}
                for j, stat in enumerate(("ret", "vol", "dd")):
                    u = wf.pit(stats[j], float(real[j][0]), rng)
                    row[stat] = (float(real[j][0]), pct(stats[j], 10), pct(stats[j], 50), pct(stats[j], 90), u)
                    pooled[h][name][stat].append(u)
                rows.append(row)

    # State volatility and crash frequency: the engine against history.
    state_label = real_states()
    sim_w = np.vstack(sim_weekly)
    sessions = np.vstack(sim_state)[:, : WEEKS * fr.PERIOD].reshape(len(sim_w), WEEKS, fr.PERIOD)
    # A week counts toward a state only if all five sessions were in it (a week with a switch is left out).
    sim_s = np.where((sessions == sessions[:, :, :1]).all(axis=2), sessions[:, :, 0], -1)
    per_year = 252
    # Daily returns of the whole history; day i belongs to week i // PERIOD (both count from SPY's first bar).
    real_d = np.diff(np.log(D["closes"]))[: T * fr.PERIOD]
    assert len(real_d) == T * fr.PERIOD and int(D["marks"][0]) == 0, "daily rows and weekly blocks must line up"
    real_day_state = np.repeat(state_label, fr.PERIOD)
    last_day = D["days"][-1]
    L = ["# Engine-level walk-forward checks (8g, second part)\n",
         f"Data through {last_day}; engine seeds 0-{PATHS - 1}; origins are the block ends nearest {', '.join(str(o) for o in origins)}.\n",
         f"{len(ns)} origins ({', '.join(str(c) for c in cutoffs)}), {PATHS} engine seeds each, 3 years forward; the regime model refit before each origin; the engine started as a new market there with only the real bars before it.\n"]
    L.append("## Volatility and drawdown ranges: engine against the regime layer alone\n")
    L.append("Each cell: realized | simulated 10th / median / 90th percentile; PIT in brackets (0 to 1, where reality fell).\n")
    for h in wf.HORIZONS:
        L.append(f"**{h // 52}-year horizon**\n")
        L.append("| origin | start | stat | real | engine | regime layer |\n|---|---|---|---|---|---|")
        for k in range(len(ns)):
            e = next(r for r in rows if r["origin"] == str(cutoffs[k]) and r["h"] == h and r["who"] == "engine")
            lyr = next(r for r in rows if r["origin"] == str(cutoffs[k]) and r["h"] == h and r["who"] == "layer")
            for stat in ("ret", "vol", "dd"):
                f = (lambda v: f"{v:.0%}") if stat == "dd" else (lambda v: f"{v:+.0%}" if stat == "ret" else f"{v:.0%}")  # noqa: E731
                cell = lambda t: f"{f(t[1])} / {f(t[2])} / {f(t[3])} [{t[4]:.2f}]"  # noqa: E731
                L.append(f"| {e['origin']} | {e['start']} | {stat} | {f(e[stat][0])} | {cell(e[stat])} | {cell(lyr[stat])} |")
        L.append("")
    L.append("**Pooled over the origins** (share of outcomes inside the central 80% range; 80% is calibrated; with 8 origins a gap under about 20 points is noise)\n")
    L.append("| horizon | stat | engine | regime layer |\n|---|---|---|---|")
    for h in wf.HORIZONS:
        for stat in ("ret", "vol", "dd"):
            cov = lambda v: f"{np.mean([(0.1 <= u <= 0.9) for u in v]):.0%}"  # noqa: E731
            L.append(f"| {h // 52}y | {stat} | {cov(pooled[h]['engine'][stat])} | {cov(pooled[h]['layer'][stat])} |")
    spread = []
    for k in range(len(ns)):
        e = next(r for r in rows if r["origin"] == str(cutoffs[k]) and r["h"] == 52 and r["who"] == "engine")["vol"]
        lyr = next(r for r in rows if r["origin"] == str(cutoffs[k]) and r["h"] == 52 and r["who"] == "layer")["vol"]
        spread.append((e[3] - e[1]) / max(lyr[3] - lyr[1], 1e-9))
    L.append(f"\nThe engine's 1-year volatility range (10th to 90th percentile) is {np.mean(spread):.2f} times the regime layer's on average (per origin: {', '.join(f'{x:.2f}' for x in spread)}).\n")

    L.append("## STATE_LEVEL_ADJ: weekly volatility by state, annualized\n")
    L.append(f"| state | engine | fitted (mean of the refits) | history (full-sample labels) | engine vs history | within ±{STATE_VOL_TOL:.0%} |\n|---|---|---|---|---|---|")
    ann = np.sqrt(252 / fr.PERIOD)
    state_ok = True
    for j, name in enumerate(STATES):
        eng = float(sim_w[sim_s == j].std() * ann) if (sim_s == j).sum() > 50 else float("nan")
        fitted = float(np.mean([p.sigma[j] for p in fits]) * ann)
        hist = float(D["r"][state_label == j].std() * ann)
        ok = bool(abs(eng / hist - 1) <= STATE_VOL_TOL) if np.isfinite(eng) else False
        state_ok &= ok
        L.append(f"| {name} | {eng:.1%} | {fitted:.1%} | {hist:.1%} | {eng / hist - 1:+.0%} | {'PASS' if ok else 'FAIL'} |")
    L.append("History's states are the full-sample fit's smoothed labels, which have hindsight and make 'crisis' a narrow set of weeks; the refits' own volatility (third column) is the fairer yardstick for the engine, and a week counts toward a state only if all five sessions were in it.\n")

    L.append("## CRASH_BY_STATE: daily SPY moves of -3% or worse, and -5% or worse, per year\n")
    sim_d = np.vstack(sim_daily)
    sim_ds = np.vstack(sim_state)
    L.append(f"| state | level | engine per year | history per year | engine / history | within a factor of {CRASH_FACTOR:g} |\n|---|---|---|---|---|---|")
    crash_ok = True
    real_d_window, real_s_window = real_d, real_day_state
    for j, name in (*list(enumerate(STATES)), (None, "all")):
        for level in CRASH_LEVELS:
            e_mask = np.ones_like(sim_d, dtype=bool) if j is None else sim_ds == j
            r_mask = np.ones_like(real_d_window, dtype=bool) if j is None else real_s_window == j
            if e_mask.sum() < 250 or r_mask.sum() < 50:
                continue
            e = float((sim_d[e_mask] <= np.log1p(level)).mean() * per_year)
            r = float((real_d_window[r_mask] <= np.log1p(level)).mean() * per_year)
            ratio = e / r if r > 0 else float("inf") if e > 0 else 1.0
            ok = 1 / CRASH_FACTOR <= ratio <= CRASH_FACTOR
            crash_ok &= ok
            L.append(f"| {name} | {level:.0%} | {e:.2f} | {r:.2f} | {ratio:.2f} | {'PASS' if ok else 'FAIL'} |")
    L.append("\nHistory is every SPY day since 1993; its daily states are the weekly smoothed labels. A state with few real days (crisis: about 40 weeks) gives a noisy rate.\n")
    L.append("## Verdicts (criteria fixed in the script's docstring)\n")
    L.append(f"- STATE_LEVEL_ADJ: {'PASS' if state_ok else 'FAIL'} (every state's engine volatility within {STATE_VOL_TOL:.0%} of history)")
    L.append(f"- CRASH_BY_STATE: {'PASS' if crash_ok else 'FAIL'} (every rate within a factor of {CRASH_FACTOR:g} of history)")
    L.append("- SHOCK_IN_CRISIS: not checked (it only acts on a user-triggered shock; history has none)\n")
    L.append("Eight origins and 4-5 real crises: this shows a gross difference, not a fine one.")
    text = "\n".join(L) + "\n"
    Path(args.report).write_text(text)
    print(text)


if __name__ == "__main__":
    main()
