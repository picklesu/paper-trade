import math
import random
import statistics
from datetime import UTC, datetime, timedelta

from paper_trade.market.engine import SECONDS_PER_YEAR, MarketEngine, bid_ask
from paper_trade.market.symbols import SymbolSpec

# 10:00 New York time, so a few hours of steps stay on the same trading day.
START = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)


def run(engine: MarketEngine, steps: int, seconds: float) -> None:
    t = engine.time
    for _ in range(steps):
        t += timedelta(seconds=seconds)
        engine.step(t)


def prices(engine: MarketEngine) -> dict[str, float]:
    return {s: q.last for s, q in engine.quotes.items()}


def test_same_seed_gives_same_prices() -> None:
    a, b = MarketEngine(START, seed=7), MarketEngine(START, seed=7)
    run(a, 500, 1)
    run(b, 500, 1)
    assert prices(a) == prices(b)


def test_different_seed_gives_different_prices() -> None:
    a, b = MarketEngine(START, seed=1), MarketEngine(START, seed=2)
    run(a, 50, 1)
    run(b, 50, 1)
    assert prices(a) != prices(b)


def test_prices_move_and_quotes_stay_consistent() -> None:
    engine = MarketEngine(START)
    start_prices = prices(engine)
    run(engine, 600, 10)
    assert prices(engine) != start_prices
    for q in engine.quotes.values():
        assert q.last > 0
        assert q.bid < q.ask
        assert q.low <= q.last <= q.high
        assert q.volume > 0


def test_step_backwards_or_same_time_is_noop() -> None:
    engine = MarketEngine(START)
    before = prices(engine)
    engine.step(START)
    engine.step(START - timedelta(seconds=5))
    assert prices(engine) == before


def test_new_day_resets_session_stats() -> None:
    engine = MarketEngine(START)
    run(engine, 60, 60)
    before = prices(engine)
    # The move during the step that crosses midnight belongs to the new day.
    engine.step(datetime(2026, 1, 6, 15, 0, tzinfo=UTC))
    for symbol, q in engine.quotes.items():
        assert q.prev_close == q.open == before[symbol]
        assert q.low == min(q.open, q.last)
        assert q.high == max(q.open, q.last)
        assert q.volume < engine._specs[symbol].daily_volume * 5


def test_realized_volatility_matches_spec() -> None:
    spec = SymbolSpec("TEST", "Test", 100, vol=0.30, drift=0, market_corr=0.5, daily_volume=1000)
    engine = MarketEngine(START, seed=3, specs=(spec,))
    engine.regime = "normal"
    engine._maybe_switch_regime = lambda dt: None  # hold the regime fixed

    step = 3600.0
    returns = []
    last = engine.quotes["TEST"].last
    t = START
    for _ in range(5000):
        t += timedelta(seconds=step)
        engine.step(t)
        now = engine.quotes["TEST"].last
        returns.append(math.log(now / last))
        last = now

    annualized = statistics.stdev(returns) * math.sqrt(SECONDS_PER_YEAR / step)
    assert abs(annualized - 0.30) < 0.02


def test_bid_ask() -> None:
    assert bid_ask(100.0) == (99.99, 100.0)
    assert bid_ask(580.0) == (579.97, 580.03)
    rng = random.Random(0)
    for price in [1.234, 57.5, 999.999] + [rng.uniform(1, 2000) for _ in range(5000)]:
        bid, ask = bid_ask(price)
        assert bid <= price <= ask
        assert ask - bid < 0.25


def test_quote_change_matches_displayed_prices() -> None:
    engine = MarketEngine(START)
    q = engine.quotes["SPY"]
    q.last, q.prev_close = 100.004, 99.996
    d = q.to_dict()
    assert d["last"] == d["prev_close"] == 100.0
    assert d["change"] == 0
    assert d["change_pct"] == 0
