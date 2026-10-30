from datetime import UTC, date, datetime, timedelta

import numpy as np
import pytest

from paper_trade.market import options as o
from paper_trade.market.calendar import MARKET_TZ, SESSION_SECONDS, session_close, session_open
from paper_trade.market.engine import MarketEngine
from paper_trade.store import Store
from paper_trade.trading import broker as broker_module
from paper_trade.trading.broker import Broker, OrderError

START = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)
FRIDAY = date(2026, 1, 16)  # AAPL's January monthly


def et(*args: int) -> datetime:
    return datetime(*args, tzinfo=MARKET_TZ)


def setup(at: datetime = et(2026, 1, 14, 10, 0), cash: float = 100_000) -> tuple[MarketEngine, Broker]:
    e = MarketEngine(START)
    e.advance(at)
    b = Broker(e, Store(":memory:"))
    b.reset(cash)
    return e, b


def contract(e: MarketEngine, right: str, moneyness: float, expiry: date = FRIDAY, symbol: str = "AAPL") -> o.Contract:
    """A listed strike near spot x moneyness."""
    strike = round(e.quotes[symbol].last * moneyness / 2.5) * 2.5
    return o.Contract(symbol, expiry, right, strike)


def advance(e: MarketEngine, b: Broker, t: datetime) -> None:
    t0 = e.time
    e.advance(t)
    b.process(t0, e.time)


def test_buying_a_call_fills_at_the_chains_ask_and_shows_as_a_position() -> None:
    e, b = setup()
    c = contract(e, "call", 1.0)
    chain = o.chain(e, "AAPL", FRIDAY)
    ask = chain["call"]["ask"][chain["strikes"].index(c.strike)]
    order = b.place(c.symbol, "buy", 2, "market")
    assert order.status == "filled" and order.fill_price == ask and order.effect == "open"
    assert b.ledger().cash == pytest.approx(100_000 - ask * 200)
    (row,) = b.summary(e.quotes)["positions"]
    assert row["option"]["label"] == c.label and row["qty"] == 2
    assert row["market_value"] == pytest.approx(row["last"] * 200, abs=0.01)
    assert 50 < row["delta"] < 150  # two contracts near the money: about 100 shares' worth
    assert row["theta"] < 0
    # Sell to close: realized against the average price.
    sale = b.place(c.symbol, "sell", 2, "market")
    assert sale.effect == "close" and b.ledger().realized_by_order[sale.id] == pytest.approx((sale.fill_price - ask) * 200)
    assert b.summary(e.quotes)["positions"] == []


def test_orders_open_or_close_never_both_and_only_market_or_limit() -> None:
    e, b = setup()
    c = contract(e, "put", 1.0)
    b.place(c.symbol, "buy", 1, "market")
    with pytest.raises(OrderError, match="sell at most 1 to close"):
        b.place(c.symbol, "sell", 2, "market")
    with pytest.raises(OrderError, match="market and limit orders only"):
        b.place(c.symbol, "sell", 1, "stop", stop_price=1.0)
    with pytest.raises(OrderError, match="no Feb 3, 2026 expiration"):
        b.place(o.Contract("AAPL", date(2026, 2, 3), "call", c.strike).symbol, "buy", 1, "market")
    with pytest.raises(OrderError, match="has expired"):
        b.place(o.Contract("AAPL", date(2026, 1, 9), "call", c.strike).symbol, "buy", 1, "market")
    with pytest.raises(OrderError, match="Unknown symbol ZZZ"):
        b.place("ZZZ260116C00100000", "buy", 1, "market")


def test_covered_calls_need_and_lock_shares() -> None:
    e, b = setup()
    c = contract(e, "call", 1.1)
    with pytest.raises(OrderError, match="covered call"):
        b.place(c.symbol, "sell", 1, "market")
    b.place("AAPL", "buy", 150, "market")
    sold = b.place(c.symbol, "sell", 1, "market")
    assert sold.effect == "open" and b.ledger().qty(c.symbol) == -1
    assert b.locked_shares("AAPL") == 100 and b.free_shares("AAPL") == 50
    with pytest.raises(OrderError, match="sell at most 50 shares of AAPL .* 100 covering calls"):
        b.place("AAPL", "sell", 60, "market")
    with pytest.raises(OrderError, match="50 shares are free"):
        b.place(c.symbol, "sell", 1, "market")
    # Shares promised to a working sell can't cover a call either.
    b.place("AAPL", "sell", 50, "limit", limit_price=10_000)
    assert b.free_shares("AAPL") == 0
    # Buy to close frees them.
    closed = b.place(c.symbol, "buy", 1, "market")
    assert closed.effect == "close" and b.locked_shares("AAPL") == 0


def test_cash_secured_puts_set_the_strike_aside() -> None:
    e, b = setup(cash=30_000)
    c = contract(e, "put", 0.9)
    bp = b.buying_power()
    preview = b.preview(c.symbol, "sell", 1, "market")
    assert preview["collateral"] == c.strike * 100 and preview["effect"] == "open"
    assert preview["max_loss"] == pytest.approx((c.strike - preview["estimated_price"]) * 100, abs=0.01)
    sold = b.place(c.symbol, "sell", 1, "market")
    assert b.collateral() == c.strike * 100
    assert b.buying_power() == pytest.approx(bp + sold.fill_price * 100 - c.strike * 100)
    assert preview["buying_power_after"] == pytest.approx(b.buying_power(), abs=0.01)
    with pytest.raises(OrderError, match="Not enough buying power"):
        b.place(c.symbol, "sell", 1, "market")
    # Buying it back may use the collateral it frees.
    preview = b.preview(c.symbol, "buy", 1, "market")
    assert preview["effect"] == "close" and preview["buying_power_after"] > b.buying_power()


def test_working_option_buys_set_cash_aside() -> None:
    e, b = setup(cash=10_000)
    c = contract(e, "call", 1.0)
    b.place(c.symbol, "buy", 10, "limit", limit_price=0.05)
    assert b.buying_power() == pytest.approx(10_000 - 0.05 * 1000)
    with pytest.raises(OrderError, match="Not enough buying power"):
        b.place(c.symbol, "buy", 2000, "limit", limit_price=0.05)


def test_option_limits_fill_at_the_first_second_the_model_reaches_them() -> None:
    """The minute-skipping search finds the same second as pricing every second."""
    e, b = setup(et(2026, 1, 13, 9, 30))
    day = date(2026, 1, 13)
    for right, side in (("call", "buy"), ("put", "buy"), ("call", "sell"), ("put", "sell")):
        c = contract(e, right, 1.0)
        col = e.symbol_index("AAPL")
        prices = e.day_prices(day, col)
        secs = np.arange(SESSION_SECONDS)
        p = o.price(e, "AAPL", FRIDAY, c.strike, day, secs, prices)
        q = getattr(p, right)["ask" if side == "buy" else "bid"][:, 0]
        limit = float(np.round(np.quantile(q[600:], 0.2 if side == "buy" else 0.8), 2))
        order = broker_module.Order(1, c.symbol, side, 1, "limit", "day", limit, None, session_open(day))
        found = b._next_option_event(order, c, day, 1, SESSION_SECONDS - 1)
        brute = np.flatnonzero((q <= limit) if side == "buy" else (q >= limit))
        brute = brute[brute >= 1]
        assert found == int(brute[0]), (right, side)


def test_a_resting_option_limit_fills_at_its_price() -> None:
    e, b = setup()
    c = contract(e, "put", 1.0)
    ask = b.option_quote(c)["ask"]
    order = b.place(c.symbol, "buy", 1, "limit", limit_price=round(ask * 0.97, 2), tif="gtc")
    assert order.status == "working"
    advance(e, b, et(2026, 1, 15, 16, 0))
    if order.status == "filled":
        assert order.fill_price <= order.limit_price
    else:
        assert order.status == "working"


def run_to_expiration(e: MarketEngine, b: Broker) -> None:
    advance(e, b, et(2026, 1, 16, 15, 59))
    advance(e, b, et(2026, 1, 20, 9, 31))  # past the close (Monday the 19th is a holiday)


def test_expiration_exercises_assigns_and_expires() -> None:
    e, b = setup(et(2026, 1, 16, 10, 0))
    b.place("AAPL", "buy", 100, "market")
    itm_call = contract(e, "call", 0.8)
    otm_call = contract(e, "call", 1.25)
    covered = contract(e, "call", 0.85)
    short_put = contract(e, "put", 1.2)
    for c, side in ((itm_call, "buy"), (otm_call, "buy"), (covered, "sell"), (short_put, "sell")):
        b.place(c.symbol, side, 1, "market")
    cash_before = b.ledger().cash
    run_to_expiration(e, b)
    led = b.ledger()
    assert all(led.qty(c.symbol) == 0 for c in (itm_call, otm_call, covered, short_put))
    summary = b.summary(e.quotes)  # closed contracts don't break the account
    assert [p["symbol"] for p in summary["positions"]] == ["AAPL"] and summary["collateral"] == 0
    # Shares: +100 exercised, -100 assigned on the call, +100 assigned on the put.
    assert led.qty("AAPL") == 200
    # Cash: pay the call's strike, get the covered call's, pay the put's.
    expected = cash_before - itm_call.strike * 100 + covered.strike * 100 - short_put.strike * 100
    assert led.cash == pytest.approx(expected, abs=0.01)
    kinds = sorted(o.type for o in b.orders.values() if o.type in ("exercise", "assignment", "expiration"))
    assert kinds == ["assignment"] * 4 + ["exercise"] * 2 + ["expiration"]
    expired = next(o for o in b.orders.values() if o.type == "expiration")
    assert expired.symbol == otm_call.symbol and expired.fill_price == 0 and expired.filled_at == session_close(FRIDAY)


def test_unaffordable_exercises_are_sold_before_the_close() -> None:
    e, b = setup(et(2026, 1, 16, 10, 0), cash=5_000)
    call = contract(e, "call", 0.9)  # exercising needs about $20,000
    put = contract(e, "put", 1.1)  # no shares to deliver
    b.place(call.symbol, "buy", 1, "market")
    b.place(put.symbol, "buy", 1, "market")
    run_to_expiration(e, b)
    sold = sorted((o for o in b.orders.values() if o.reason and o.reason.startswith("Sold before expiration")), key=lambda o: o.symbol)
    assert [o.symbol for o in sold] == sorted([call.symbol, put.symbol])
    assert all(o.filled_at == session_open(FRIDAY) + timedelta(seconds=SESSION_SECONDS - 60) for o in sold)
    assert all(o.fill_price > 0 for o in sold) and b.ledger().qty("AAPL") == 0


def test_early_exercise() -> None:
    e, b = setup()
    c = contract(e, "call", 0.8)
    b.place(c.symbol, "buy", 1, "market")
    preview = b.preview_exercise(c.symbol, 1)
    assert preview["cash_change"] == -c.strike * 100 and preview["shares"] == 100
    cash = b.ledger().cash
    records = b.exercise(c.symbol, 1)
    assert [r.type for r in records] == ["exercise", "exercise"]
    assert b.ledger().qty("AAPL") == 100 and b.ledger().qty(c.symbol) == 0
    assert b.ledger().cash == pytest.approx(cash - c.strike * 100, abs=0.01)
    with pytest.raises(OrderError, match="don't hold"):
        b.exercise(c.symbol, 1)
    put = contract(e, "put", 1.2)
    b.place(put.symbol, "buy", 2, "market")
    with pytest.raises(OrderError, match="100 are free"):
        b.exercise(put.symbol, 2)  # 200 shares needed, 100 owned


def test_early_assignment_repeats_on_a_replay(monkeypatch) -> None:
    monkeypatch.setattr(broker_module, "EARLY_ASSIGNMENT", 1.0)
    monkeypatch.setattr(broker_module, "EARLY_TIME_VALUE", 10.0)

    def assigned_at() -> list[datetime]:
        e, b = setup(et(2026, 1, 13, 10, 0))
        c = contract(e, "put", 1.25)  # deep in the money: little time value
        b.place(c.symbol, "sell", 1, "market")
        advance(e, b, et(2026, 1, 15, 10, 0))
        return [o.filled_at for o in b.orders.values() if o.type == "assignment"]

    first = assigned_at()
    assert first and first[0] < session_close(FRIDAY) and first == assigned_at()


def test_going_back_before_expiration_brings_the_contracts_back() -> None:
    e, b = setup(et(2026, 1, 16, 10, 0))
    c = contract(e, "call", 0.9)
    b.place(c.symbol, "buy", 1, "market")
    run_to_expiration(e, b)
    assert b.ledger().qty(c.symbol) == 0 and b.ledger().qty("AAPL") == 100
    t = et(2026, 1, 16, 12, 0)
    preview = b.preview_rewind(t)
    assert [p["symbol"] for p in preview["positions"]] == [c.symbol]
    e.rewind(t, new_future=False)
    b.rewind(t)
    assert b.ledger().qty(c.symbol) == 1 and b.ledger().qty("AAPL") == 0
    assert not any(o.type in ("exercise", "expiration") for o in b.orders.values())


def test_day_pnl_counts_options_from_the_previous_close() -> None:
    e, b = setup(et(2026, 1, 13, 10, 0))
    c = contract(e, "call", 1.0)
    b.place(c.symbol, "buy", 1, "market")
    advance(e, b, et(2026, 1, 14, 9, 30, 1))
    s = b.summary(e.quotes)
    # At the open, the day's P&L is just how far the mark moved from yesterday's closing mark.
    mark_then = b._mark_at_close(c, date(2026, 1, 13))
    assert s["day_pnl"] == pytest.approx((s["positions"][0]["last"] - mark_then) * 100, abs=1.0)


def test_held_contracts_stay_listed_when_the_pattern_drops_them() -> None:
    from paper_trade.market.clock import SimClock
    from paper_trade.market.service import MarketService

    e = MarketEngine(START)
    e.advance(et(2026, 1, 14, 10, 0))
    svc = MarketService(SimClock(start=e.time, real_time=lambda: 0.0), e, Store(":memory:"), history_days=5)
    weekly = date(2026, 1, 23)
    c = contract(e, "call", 1.03, weekly)
    svc.broker.place(c.symbol, "buy", 1, "market")
    # A calibration whose pattern lists no weeklies.
    no_weeklies = o.DEFAULT_PATTERN | {"weekly_days": -1}
    svc.calibrations = {"AAPL": o.Calibration(pattern=no_weeklies)}
    assert weekly not in {x.day for x in o.expirations("AAPL", e.time, no_weeklies)}
    assert weekly.isoformat() in [x["date"] for x in svc.option_expirations("AAPL")]
    chain = svc.option_chain("AAPL", weekly)
    assert c.strike in chain["strikes"]
    svc.broker.place(c.symbol, "sell", 1, "market")  # still tradable to close
