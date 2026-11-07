"""Check that simulated release days are as busy as the event study found (no real data needed).

    uv run python scripts/check_releases.py [--years 10] [--seeds 4]

Simulates SPY (the event study's symbol; AAPL and KO ride along) from a new market (no real past) and, per release kind in `calendar.RELEASES`,
prints the mean |close-to-close move| and the volume on those days against ordinary sessions (no release, not
next to a holiday), next to the multiples the model was given. The move multiple is the mean of the
absolute moves' ratio to the mean of ordinary days', as in `event_study.py`.
"""

from __future__ import annotations

import argparse
from datetime import date

import numpy as np

from paper_trade.market import calendar as cal
from paper_trade.market import engine as engine_mod
from paper_trade.market.engine import MarketEngine


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=10)
    ap.add_argument("--seeds", type=int, default=4)
    a = ap.parse_args()
    specs = tuple(s for s in engine_mod.SYMBOLS if s.symbol in ("SPY", "AAPL", "KO"))
    start = cal.session_open(date(2031, 1, 6))
    moves: dict[str, list[float]] = {k: [] for k in (*cal.RELEASES, "none")}
    volumes: dict[str, list[float]] = {k: [] for k in moves}
    for seed in range(a.seeds):
        engine = MarketEngine(start, seed=seed, specs=specs)
        days, close, volume = [], [], []

        def done(day: date, engine=engine, days=days, close=close, volume=volume) -> None:
            sk = engine._skeleton(day)
            days.append(day)
            close.append(np.exp(sk.close[-1]))
            volume.append(sk.volume.sum(axis=0))

        engine.on_day_complete = done
        engine.advance(cal.add_trading_seconds(start, a.years * 252 * cal.SESSION_SECONDS))
        close, volume = np.array(close), np.array(volume)
        ret = np.abs(np.diff(np.log(close), axis=0))
        ordinary = np.array([not cal.release_days(d) and cal.day_effects(d) == (1.0, 1.0) and not cal.volume_event(d) for d in days])
        for i in range(60, len(days) - 1):
            window = np.arange(i - 60, i)
            window = window[ordinary[window]]
            kinds = cal.release_days(days[i + 1]) or (["none"] if ordinary[i + 1] else [])
            if len(kinds) != 1:
                continue  # days with two releases belong to neither
            moves[kinds[0]].append(float((ret[i] / ret[window - 1].mean(axis=0))[0]))
            volumes[kinds[0]].append(float((volume[i + 1] / np.median(volume[window], axis=0))[0]))
    base_move, base_volume = np.mean(moves["none"]), np.mean(volumes["none"])  # (a ratio to a trailing mean is above 1 even for ordinary days)
    print(f"{'':8s} {'days':>6s} {'move x':>8s} {'model':>7s} {'volume x':>9s} {'model':>7s}   (against ordinary days)")
    for k in cal.RELEASES:
        v, m, _ = cal.RELEASES[k]
        print(f"{k:8s} {len(moves[k]):6d} {np.mean(moves[k]) / base_move:8.2f} {m:7.2f} {np.mean(volumes[k]) / base_volume:9.2f} {v:7.2f}")
    print(f"{'ordinary':8s} {len(moves['none']):6d}")


if __name__ == "__main__":
    main()
