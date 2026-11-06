"""Measure the engine's holiday effects against the real ones.

    uv run python scripts/check_holidays.py [--years 30] [--seeds 6]

Simulated sessions (no real data, three symbols) are grouped as in the real data:
early closes, the session before and after a weekday holiday, Thanksgiving Eve and the
week from December 26. Each group's median volume and median absolute move, against the
median of the previous 60 ordinary sessions, is printed next to the real figure (20 years
of the 40 symbols' daily bars, measured the same way; the REAL table below).
"""

from __future__ import annotations

import argparse
from datetime import date, datetime
from multiprocessing import Pool

import numpy as np

from paper_trade.market import calendar
from paper_trade.market import engine as engine_mod
from paper_trade.market.engine import MarketEngine
from paper_trade.market.symbols import SYMBOLS

START = datetime(2026, 1, 5, 9, 30, tzinfo=calendar.MARKET_TZ)
REAL = {"early close": (0.46, 0.71), "before holiday": (0.89, 0.79), "after holiday": (0.97, 1.12),
        "Thanksgiving Eve": (0.81, 0.78), "Dec 26 - Jan 2": (0.66, 0.70), "normal": (0.99, 0.99)}


def group(d: date) -> str:
    if calendar.is_early_close(d):
        return "early close"
    effects = calendar.day_effects(d)
    if effects == calendar.EFFECT_CHRISTMAS_WEEK:
        return "Dec 26 - Jan 2"
    if effects == calendar.EFFECT_THANKSGIVING_EVE:
        return "Thanksgiving Eve"
    if effects == calendar.EFFECT_BEFORE_HOLIDAY:
        return "before holiday"
    if effects == calendar.EFFECT_AFTER_HOLIDAY:
        return "after holiday"
    return "normal"


def one(args: tuple[int, int]) -> tuple[list[date], np.ndarray, np.ndarray]:
    seed, years = args
    engine = MarketEngine(START, seed=seed, specs=SYMBOLS[:3])
    days, closes, volume = [], [], []

    def done(day: date) -> None:
        sk = engine._skeleton(day)
        days.append(day)
        closes.append(float(sk.close[-1, 0]))
        volume.append(float(sk.volume[:, 0].sum()))

    engine.on_day_complete = done
    engine.advance(calendar.add_trading_seconds(START, years * 252 * 23_400))
    return days, np.array(closes), np.array(volume)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=30)
    ap.add_argument("--seeds", type=int, default=6)
    a = ap.parse_args()
    with Pool(4) as pool:
        runs = pool.map(one, [(s, a.years) for s in range(a.seeds)])
    acc: dict[str, list[tuple[float, float]]] = {}
    for days, close, volume in runs:
        move = np.abs(np.diff(close, prepend=np.nan))
        groups = [group(d) for d in days]
        ordinary = np.array([g == "normal" for g in groups])
        for i in range(60, len(days)):
            m = ordinary[i - 60 : i]
            if m.sum() < 20:
                continue
            base_v, base_m = np.median(volume[i - 60 : i][m]), np.nanmedian(move[i - 60 : i][m])
            if base_v > 0 and base_m > 0:
                acc.setdefault(groups[i], []).append((volume[i] / base_v, move[i] / base_m))
    print(f"{a.seeds * a.years} simulated years; median volume / median absolute move against the previous 60 ordinary sessions")
    print(f"{'group':18s} {'n':>6s}  {'engine':>13s}   {'real':>13s}")
    for name, vals in sorted(acc.items()):
        v = np.array(vals)
        print(f"{name:18s} {len(v):6d}  {np.median(v[:, 0]):5.2f} / {np.median(v[:, 1]):5.2f}   {REAL[name][0]:5.2f} / {REAL[name][1]:5.2f}")


if __name__ == "__main__":
    main()
