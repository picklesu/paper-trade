"""Engine benchmark: time and memory for the work that grows with the symbol count.

    uv run python scripts/bench_engine.py [symbols]

Measures one session's generation, a year's fast-forward, the 5-year history
build (generation plus the 30-minute bars it stores), and one tick's quotes.
"""

import sys
import time
import tracemalloc
from dataclasses import replace
from datetime import datetime

from paper_trade.market.calendar import MARKET_TZ, add_trading_seconds
from paper_trade.market.engine import MarketEngine
from paper_trade.market.history import STORED_MINUTES, aggregate
from paper_trade.market.symbols import SYMBOLS

START = datetime(2026, 1, 5, 9, 30, tzinfo=MARKET_TZ)


def specs(n: int) -> tuple:
    if n <= len(SYMBOLS):
        return SYMBOLS[:n]
    return tuple(replace(SYMBOLS[i % len(SYMBOLS)], symbol=f"{SYMBOLS[i % len(SYMBOLS)].symbol}{i}") for i in range(n))


def timed(label: str, fn) -> None:
    began = time.perf_counter()
    fn()
    print(f"{label:28} {time.perf_counter() - began:8.3f} s")


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else len(SYMBOLS)
    print(f"{n} symbols")

    engine = MarketEngine(START, specs=specs(n))
    timed("fast-forward 1 year", lambda: engine.advance(add_trading_seconds(START, 252 * 23_400)))

    engine = MarketEngine(START, specs=specs(n))

    def backfill() -> None:
        for day in engine.backfill_iter(1260):
            aggregate(engine.minute_bars(day), STORED_MINUTES)

    timed("history build (5 years)", backfill)

    engine = MarketEngine(START, specs=specs(n))
    t = add_trading_seconds(START, 3600)
    engine.advance(t)
    engine.quotes  # warm
    began = time.perf_counter()
    for _ in range(200):
        t = add_trading_seconds(t, 1)
        engine.advance(t)
        engine.quotes
    print(f"{'one tick (1x speed)':28} {(time.perf_counter() - began) / 200 * 1e3:8.3f} ms")

    tracemalloc.start()
    engine = MarketEngine(START, specs=specs(n))
    engine.advance(add_trading_seconds(START, 5 * 23_400))
    print(f"{'memory, 5 sessions cached':28} {tracemalloc.get_traced_memory()[1] / 1e6:8.1f} MB peak")


if __name__ == "__main__":
    main()
