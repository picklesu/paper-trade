"""Measure the engine's regime levels with no real data: SPY's weekly volatility and
the rate of -3% / -5% days in each state, and the whole run's volatility and drift.

    uv run python scripts/check_engine_states.py [--years 40] [--seeds 16] \
        [--adj 0.95,0.95,2.4] [--crash 0.3,1.2,4.0]

Used to retune `STATE_LEVEL_ADJ` and `CRASH_BY_STATE` against what the walk-forward
checks found (`walk_forward_engine_report.md`): history's weekly volatility by state is
8.9% / 20.8% / 52.2% (calm / choppy / crisis; full-sample labels) and its -3% days a
year are 0.30 / 4.7 / 49 (-5%: 0 / 0.54 / 20). With the crisis at 2.4 it measures
51.5%, and its -3% days 38 a year (1.8 gave 43-47% and 33). Three symbols run (SPY and its factor
neighbours), which is far quicker than 40 and gives SPY the same path.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from multiprocessing import Pool

import numpy as np

from paper_trade.market import calendar
from paper_trade.market import engine as engine_mod
from paper_trade.market.engine import MarketEngine
from paper_trade.market.symbols import SYMBOLS

START = datetime(2026, 1, 5, 9, 30, tzinfo=calendar.MARKET_TZ)
PERIOD = 5


def one(args: tuple[int, int, tuple, tuple]) -> tuple[np.ndarray, np.ndarray]:
    seed, years, adj, crash = args
    engine_mod.STATE_LEVEL_ADJ = adj
    engine_mod.CRASH_BY_STATE = crash
    engine = MarketEngine(START, seed=seed, specs=SYMBOLS[:3])
    closes, states = [], []

    def done(day) -> None:
        sk = engine._skeleton(day)
        closes.append(float(sk.close[-1, 0]))
        states.append(sk.state)

    engine.on_day_complete = done
    engine.advance(calendar.add_trading_seconds(START, years * 252 * 23_400))
    return np.diff(np.concatenate([[np.log(SYMBOLS[0].price)], closes])), np.array(states)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=40)
    ap.add_argument("--seeds", type=int, default=16)
    ap.add_argument("--adj", default=",".join(str(x) for x in engine_mod.STATE_LEVEL_ADJ))
    ap.add_argument("--crash", default=",".join(str(x) for x in engine_mod.CRASH_BY_STATE))
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    adj, crash = tuple(float(x) for x in a.adj.split(",")), tuple(float(x) for x in a.crash.split(","))
    with Pool(a.workers) as pool:
        runs = pool.map(one, [(s, a.years, adj, crash) for s in range(a.seeds)])
    d = np.concatenate([r[0] for r in runs])
    st = np.concatenate([r[1] for r in runs])
    per = len(runs[0][0])
    assert per % PERIOD == 0, "use a number of years whose sessions fill whole weeks (a week must not span two seeds)"
    print(f"adj {adj} crash {crash}: {len(d) / 252:.0f} simulated years")
    print(f"whole run: vol {d.std() * np.sqrt(252):.1%}  mean log return {d.mean() * 252:+.1%}/yr")
    weeks = len(d) // PERIOD
    w = d[: weeks * PERIOD].reshape(weeks, PERIOD)
    s5 = st[: weeks * PERIOD].reshape(weeks, PERIOD)
    whole = (s5 == s5[:, :1]).all(axis=1)
    for k, name in enumerate(engine_mod.STATES):
        sel = whole & (s5[:, 0] == k)
        days = st == k
        rates = [(d[days] <= np.log1p(x)).mean() * 252 for x in (-0.03, -0.05)]
        print(f"{name:7s} {days.mean():6.1%} of time  weekly vol {w[sel].sum(axis=1).std() * np.sqrt(52):6.1%}  "
              f"-3% days {rates[0]:5.2f}/yr  -5% days {rates[1]:5.2f}/yr")
    print(f"all     -3% days {(d <= np.log1p(-0.03)).mean() * 252:.2f}/yr  -5% days {(d <= np.log1p(-0.05)).mean() * 252:.2f}/yr")


if __name__ == "__main__":
    main()
