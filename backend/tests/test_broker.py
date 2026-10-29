from datetime import date, datetime, timedelta

import numpy as np
import pytest

from paper_trade.market.calendar import MARKET_TZ, SESSION_SECONDS, market_day, session_open, session_seconds
from paper_trade.market.engine import MarketEngine, bid_ask, bid_ask_arrays
from paper_trade.store import Store
from paper_trade.trading.broker import STARTING_CASH, Broker, OrderError


def et(*args: int) -> datetime:
    return datetime(*args, tzinfo=MARKET_TZ)


START = et(2026, 1, 5, 9, 30)  # Monday open
T = et(2026, 1, 5, 10, 0)


def make(t: datetime = T, store: Store | None = None) -> Broker:
    engine = MarketEngine(START)
    engine.advance(t)
    return Broker(engine, store or Store(":memory:"))


def move(b: Broker, t: datetime) -> None:
    """Advance the market and match orders, like the service does."""
    t0 = b.engine.time
    b.engine.advance(t)
    b.process(t0, b.engine.time)


def path(b: Broker, symbol: str, day: date) -> np.ndarray:
    return b.engine.day_prices(day)[:, b.engine.symbol_index(symbol)]


def first_second(cond: np.ndarray, after: int) -> int:
    return after + 1 + int(np.flatnonzero(cond[after + 1 :])[0])


# ----- market orders, cash and positions -------------------------------------


def test_market_buy_fills_at_ask() -> None:
    b = make()
    ask = b.engine.quotes["AAPL"].ask
    o = b.place("AAPL", "buy", 10, "market")
    assert o.status == "filled"
    assert o.fill_price == ask
    assert o.filled_at == T
    led = b.ledger()
    assert led.cash == pytest.approx(STARTING_CASH - 10 * ask)
    assert led.positions["AAPL"].qty == 10
    assert led.positions["AAPL"].avg_cost == pytest.approx(ask)


def test_round_trip_realizes_spread() -> None:
    b = make()
    q = b.engine.quotes["SPY"]
    b.place("SPY", "buy", 5, "market")
    b.place("SPY", "sell", 5, "market")
    led = b.ledger()
    assert led.positions["SPY"].qty == 0
    assert led.positions["SPY"].realized == pytest.approx(5 * (q.bid - q.ask))
    assert led.cash == pytest.approx(STARTING_CASH + 5 * (q.bid - q.ask))


def test_average_cost_across_buys() -> None:
    b = make()
    a1 = b.place("MSFT", "buy", 10, "market").fill_price
    move(b, et(2026, 1, 5, 11, 0))
    a2 = b.place("MSFT", "buy", 30, "market").fill_price
    assert b.ledger().positions["MSFT"].avg_cost == pytest.approx((10 * a1 + 30 * a2) / 40)


def test_each_filled_sell_reports_its_realized_pnl_against_average_cost() -> None:
    b = make()
    buy1 = b.place("MSFT", "buy", 10, "market")
    move(b, et(2026, 1, 5, 11, 0))
    buy2 = b.place("MSFT", "buy", 30, "market")
    avg = (10 * buy1.fill_price + 30 * buy2.fill_price) / 40
    move(b, et(2026, 1, 5, 12, 0))
    sell1 = b.place("MSFT", "sell", 15, "market")
    move(b, et(2026, 1, 5, 13, 0))
    sell2 = b.place("MSFT", "sell", 25, "market")
    assert b.order_dict(buy1)["realized"] is None
    assert b.order_dict(sell1)["realized"] == pytest.approx(15 * (sell1.fill_price - avg))
    assert b.order_dict(sell2)["realized"] == pytest.approx(25 * (sell2.fill_price - avg))
    # Together they make up the position's realized P&L, exactly (not rounded per order).
    total = b.order_dict(sell1)["realized"] + b.order_dict(sell2)["realized"]
    assert total == pytest.approx(b.ledger().positions["MSFT"].realized)
    # A working sell has realized nothing yet.
    b.place("MSFT", "buy", 5, "market")
    working = b.place("MSFT", "sell", 5, "limit", limit_price=10_000)
    assert b.order_dict(working)["realized"] is None


def test_not_enough_buying_power() -> None:
    b = make()
    with pytest.raises(OrderError, match="Not enough buying power"):
        b.place("SPY", "buy", 1000, "market")
    assert not b.orders


def test_working_buys_reserve_buying_power() -> None:
    b = make()
    last = b.engine.quotes["SPY"].last
    b.place("SPY", "buy", 100, "limit", limit_price=round(last * 0.9, 2))
    assert b.buying_power() == pytest.approx(STARTING_CASH - 100 * round(last * 0.9, 2))
    with pytest.raises(OrderError, match="Not enough buying power"):
        b.place("SPY", "buy", 100, "limit", limit_price=round(last * 0.9, 2))


def test_no_short_selling() -> None:
    b = make()
    with pytest.raises(OrderError, match="Short selling"):
        b.place("TSLA", "sell", 1, "market")


def test_cannot_sell_more_than_owned_or_already_committed() -> None:
    b = make()
    b.place("AMD", "buy", 10, "market")
    with pytest.raises(OrderError, match="at most 10"):
        b.place("AMD", "sell", 11, "market")
    b.place("AMD", "sell", 6, "limit", limit_price=9999)
    with pytest.raises(OrderError, match="at most 4"):
        b.place("AMD", "sell", 5, "market")


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"symbol": "NOPE", "qty": 1, "type": "market"}, "Unknown symbol"),
        ({"symbol": "SPY", "qty": 0, "type": "market"}, "Quantity"),
        ({"symbol": "SPY", "qty": 1, "type": "limit"}, "Limit price"),
        ({"symbol": "SPY", "qty": 1, "type": "stop", "stop_price": -1}, "Stop price"),
    ],
)
def test_invalid_orders(kwargs: dict, message: str) -> None:
    with pytest.raises(OrderError, match=message):
        make().place(side="buy", **kwargs)


# ----- resting orders against the price path ---------------------------------


def test_resting_buy_limit_fills_at_limit_on_first_cross() -> None:
    b = make()
    day = market_day(T)
    x = path(b, "NVDA", day)
    _, ask = bid_ask_arrays(x)
    s0 = int(session_seconds(T))
    limit = round(float(ask[s0:].min()), 2)  # the ask reaches it later today, not now
    o = b.place("NVDA", "buy", 10, "limit", limit_price=limit)
    assert o.status == "working"

    move(b, et(2026, 1, 5, 15, 59, 59))  # one big step, like 600x or a jump
    k = first_second(ask <= limit, s0)
    assert o.status == "filled"
    assert o.fill_price == limit
    assert o.filled_at == session_open(day) + timedelta(seconds=k)


def test_marketable_limit_fills_at_ask() -> None:
    b = make()
    q = b.engine.quotes["QQQ"]
    o = b.place("QQQ", "buy", 1, "limit", limit_price=round(q.ask + 5, 2))
    assert o.status == "filled"
    assert o.fill_price == q.ask


def test_sell_stop_triggers_and_fills_at_bid() -> None:
    b = make()
    b.place("TSLA", "buy", 10, "market")
    day = market_day(T)
    x = path(b, "TSLA", day)
    s0 = int(session_seconds(T))
    stop = round(float(x[s0:].min()) + 0.05, 2)
    o = b.place("TSLA", "sell", 10, "stop", stop_price=stop)
    assert o.status == "working"

    move(b, et(2026, 1, 5, 15, 59, 59))
    k = first_second(x <= stop, s0)
    assert o.status == "filled"
    assert o.triggered_at == o.filled_at == session_open(day) + timedelta(seconds=k)
    assert o.fill_price == bid_ask(float(x[k]), b.engine.spread_factor(o.filled_at))[0]


def test_stop_limit_waits_for_stop_then_acts_as_limit() -> None:
    b = make()
    day = market_day(T)
    s0 = int(session_seconds(T))
    # A symbol that rises well above its current price later in the day.
    symbol = next(s.symbol for s in b.engine.specs if path(b, s.symbol, day)[s0:].max() > path(b, s.symbol, day)[s0] + 0.2)
    x = path(b, symbol, day)
    stop = round(float(x[s0:].max()) - 0.05, 2)  # buy stop above the market, reached later
    limit = round(stop + 50, 2)  # generous: fills right when triggered
    o = b.place(symbol, "buy", 10, "stop_limit", stop_price=stop, limit_price=limit)
    assert o.status == "working" and o.triggered_at is None

    move(b, et(2026, 1, 5, 15, 59, 59))
    k = first_second(x >= stop, s0)
    assert o.triggered_at == session_open(day) + timedelta(seconds=k)
    assert o.status == "filled"
    assert o.fill_price == bid_ask(float(x[k]), b.engine.spread_factor(o.filled_at))[1]


def test_day_order_expires_at_close_gtc_does_not() -> None:
    b = make()
    low = round(b.engine.quotes["SPY"].last * 0.5, 2)  # never reached
    day_order = b.place("SPY", "buy", 1, "limit", limit_price=low, tif="day")
    gtc = b.place("SPY", "buy", 1, "limit", limit_price=low, tif="gtc")
    move(b, et(2026, 1, 6, 10, 0))
    assert day_order.status == "expired"
    assert day_order.closed_at == et(2026, 1, 5, 16, 0)
    assert gtc.status == "working"


def test_gtc_fills_on_a_later_day_during_a_long_jump() -> None:
    b = make()
    last = b.engine.quotes["TSLA"].last
    limit = round(last * 0.93, 2)  # TSLA is volatile: reached within weeks
    o = b.place("TSLA", "buy", 10, "limit", limit_price=limit, tif="gtc")
    move(b, et(2026, 3, 2, 10, 0))
    assert o.status == "filled"
    assert market_day(o.filled_at) > market_day(T)
    assert o.fill_price <= limit


def test_limit_fills_at_better_price_when_open_gaps_through() -> None:
    # Find a session that gaps down at the open, then rest a buy limit between the
    # previous close and the open: it should fill at the (better) opening ask.
    probe = make(et(2026, 6, 1, 10, 0))
    days = sorted(probe.engine._starts)
    i = probe.engine.symbol_index("NVDA")
    for prev, day in zip(days, days[1:]):
        close = float(probe.engine.day_prices(prev)[-1, i])
        open_ = float(probe.engine.day_prices(day)[0, i])
        if open_ < close * 0.995:
            break
    else:
        pytest.fail("no gap down found")

    b = make(session_open(prev) + timedelta(seconds=SESSION_SECONDS - 1))  # last second of `prev`
    limit = round((close + open_) / 2, 2)
    o = b.place("NVDA", "buy", 1, "limit", limit_price=limit, tif="gtc")
    assert o.status == "working"
    move(b, session_open(day) + timedelta(minutes=1))
    assert o.filled_at == session_open(day)
    assert o.fill_price == bid_ask(open_, b.engine.spread_factor(o.filled_at))[1]
    assert o.fill_price < limit


def test_cancel() -> None:
    b = make()
    o = b.place("SPY", "buy", 1, "limit", limit_price=1.0)
    b.cancel(o.id)
    assert o.status == "canceled"
    assert b.buying_power() == STARTING_CASH
    with pytest.raises(OrderError, match="already canceled"):
        b.cancel(o.id)


# ----- account summary -----------------------------------------------------------


def test_summary_values_positions_and_day_pnl() -> None:
    b = make()
    b.place("SPY", "buy", 10, "market")
    cash = b.ledger().cash
    move(b, et(2026, 1, 6, 11, 0))  # next day: day P&L is measured from the previous close
    quotes = b.engine.quotes
    s = b.summary(quotes)
    spy = quotes["SPY"]
    assert s["cash"] == pytest.approx(cash, abs=0.01)
    assert s["net_liq"] == pytest.approx(cash + 10 * spy.last, abs=0.02)
    assert s["day_pnl"] == pytest.approx(10 * (spy.last - spy.prev_close), abs=0.02)
    [pos] = s["positions"]
    assert pos["qty"] == 10
    assert pos["unrealized"] == pytest.approx(10 * (spy.last - pos["avg_cost"]), abs=0.02)


# ----- time travel and persistence -------------------------------------------------


def test_rewind_erases_later_trades_and_refunds() -> None:
    b = make()
    b.place("AAPL", "buy", 10, "market")  # before the rewind point: kept
    move(b, et(2026, 1, 5, 11, 0))
    cash_at_11 = b.ledger().cash
    rewind_to = b.engine.time
    move(b, et(2026, 1, 5, 12, 0))
    later = b.place("MSFT", "buy", 20, "market")
    working = b.place("AAPL", "sell", 10, "limit", limit_price=9999, tif="gtc")

    preview = b.preview_rewind(rewind_to)
    assert [f["id"] for f in preview["erased_fills"]] == [later.id]
    assert preview["erased_orders"] == 2
    assert preview["cash_then"] == pytest.approx(cash_at_11, abs=0.01)
    assert [p["symbol"] for p in preview["positions"]] == ["AAPL"]

    b.engine.rewind(rewind_to, new_future=False)
    b.rewind(rewind_to)
    assert later.id not in b.orders and working.id not in b.orders
    assert b.ledger().cash == pytest.approx(cash_at_11)
    assert "MSFT" not in b.ledger().positions


def test_rewind_reopens_orders_closed_later() -> None:
    b = make()
    low = round(b.engine.quotes["SPY"].last * 0.5, 2)
    o = b.place("SPY", "buy", 1, "limit", limit_price=low)
    move(b, et(2026, 1, 6, 10, 0))
    assert o.status == "expired"
    b.engine.rewind(et(2026, 1, 5, 12, 0), new_future=False)
    b.rewind(et(2026, 1, 5, 12, 0))
    assert o.status == "working" and o.closed_at is None


def test_replay_after_rewind_refills_identically() -> None:
    b = make()
    day = market_day(T)
    x = path(b, "META", day)
    s0 = int(session_seconds(T))
    limit = round(float(bid_ask_arrays(x)[1][s0:].min()), 2)
    o = b.place("META", "buy", 1, "limit", limit_price=limit)
    move(b, et(2026, 1, 5, 15, 0))
    first = (o.filled_at, o.fill_price)
    b.engine.rewind(T, new_future=False)
    b.rewind(T)
    assert o.status == "working"
    move(b, et(2026, 1, 5, 15, 0))
    assert (o.filled_at, o.fill_price) == first


def test_reset_account() -> None:
    b = make()
    b.place("SPY", "buy", 1, "market")
    b.reset(50_000)
    assert not b.orders
    assert b.ledger().cash == 50_000
    with pytest.raises(OrderError):
        b.reset(-5)


def test_orders_and_cash_survive_reload(tmp_path) -> None:
    store = Store(tmp_path / "t.db")
    b = make(store=store)
    b.reset(25_000)
    b.place("AAPL", "buy", 10, "market")
    b.place("AAPL", "sell", 5, "limit", limit_price=9999, tif="gtc")
    store.close()

    again = Broker(b.engine, Store(tmp_path / "t.db"))
    assert again.starting_cash == 25_000
    assert {o.id: o.to_dict() for o in again.orders.values()} == {o.id: o.to_dict() for o in b.orders.values()}
    assert again.ledger().cash == pytest.approx(b.ledger().cash)


def test_limit_at_last_price_does_not_fill_inside_the_spread() -> None:
    b = make()
    q = b.engine.quotes["SPY"]
    o = b.place("SPY", "buy", 1, "limit", limit_price=round(q.last, 2))
    move(b, add_seconds(T, 1))
    if o.status == "filled":  # only if the ask itself came down to the limit
        x = path(b, "SPY", market_day(T))
        factor = b.engine.spread_factor(o.filled_at)
        assert bid_ask(float(x[int(session_seconds(o.filled_at))]), factor)[1] <= o.limit_price


def test_buy_stop_reserves_at_least_the_ask() -> None:
    b = make()
    b.reset(10_000)
    ask = b.engine.quotes["SPY"].ask
    with pytest.raises(OrderError, match="Not enough buying power"):
        b.place("SPY", "buy", 100, "stop", stop_price=1.0)  # already triggered: fills at the ask
    o = b.place("SPY", "buy", 5, "stop", stop_price=round(ask * 1.5, 2))
    assert b.buying_power() == pytest.approx(10_000 - 5 * round(ask * 1.5, 2))
    assert o.status == "working"


def test_fill_cannot_use_cash_set_aside_for_other_orders() -> None:
    from paper_trade.trading.orders import Order

    b = make()
    b.reset(10_000)
    b.place("AAPL", "buy", 90, "limit", limit_price=100.0)  # sets aside $9,000
    assert b.buying_power() == pytest.approx(1_000)
    # A $2,000 fill fits in total cash but not in the cash that's free.
    o = Order(99, "AAPL", "buy", 10, "stop", "day", None, 1.0, b.engine.time)
    b.orders[o.id] = o
    b._fill(o, 200.0, b.engine.time)
    assert o.status == "rejected"
    assert "Not enough cash" in o.reason
    assert b.ledger().cash == 10_000


@pytest.mark.parametrize("bad", [float("inf"), float("nan"), 0, -1, 2_000_000])
def test_rejects_unreasonable_prices(bad: float) -> None:
    with pytest.raises(OrderError, match="Limit price"):
        make().place("SPY", "buy", 1, "limit", limit_price=bad)


def test_start_of_day_value_is_cached_until_fills_change() -> None:
    b = make()
    b.place("SPY", "buy", 1, "market")
    quotes = b.engine.quotes
    b.summary(quotes)
    cached = b._day_start
    b.summary(quotes)
    assert b._day_start is cached
    b.place("SPY", "buy", 1, "market")
    assert b._day_start is None


def add_seconds(t: datetime, s: int) -> datetime:
    return t + timedelta(seconds=s)


# ----- preview ---------------------------------------------------------------------


def test_preview_market_buy_matches_the_fill() -> None:
    b = make()
    q = b.engine.quotes["AAPL"]
    p = b.preview("AAPL", "buy", 10, "market")
    assert p["fills_now"] is True
    assert p["estimated_price"] == q.ask
    assert p["estimated_total"] == pytest.approx(10 * q.ask, abs=0.01)
    assert p["buying_power_after"] == pytest.approx(STARTING_CASH - 10 * q.ask, abs=0.01)
    assert (p["position_before"], p["position_after"]) == (0, 10)
    assert p["commission"] == 0
    assert not b.orders  # nothing placed

    o = b.place("AAPL", "buy", 10, "market")
    assert o.fill_price == p["estimated_price"]
    assert b.buying_power() == pytest.approx(p["buying_power_after"], abs=0.01)


def test_preview_resting_limit_reserves_and_warns() -> None:
    b = make()
    last = b.engine.quotes["SPY"].last
    limit = round(last * 0.85, 2)
    p = b.preview("SPY", "buy", 10, "limit", limit_price=limit)
    assert p["fills_now"] is False
    assert p["estimated_price"] == limit
    assert p["buying_power_after"] == pytest.approx(STARTING_CASH - 10 * limit, abs=0.01)
    text = " ".join(p["warnings"])
    assert "15% away" in text
    assert "expires" in text


def test_preview_marketable_limit_and_triggered_stop() -> None:
    b = make()
    q = b.engine.quotes["MSFT"]
    p = b.preview("MSFT", "buy", 1, "limit", limit_price=round(q.ask + 1, 2))
    assert p["fills_now"] and p["estimated_price"] == q.ask
    assert any("fills right away" in w for w in p["warnings"])

    p = b.preview("MSFT", "buy", 1, "stop", stop_price=round(q.last * 0.9, 2))
    assert p["fills_now"] and p["estimated_price"] == q.ask
    assert any("already reached" in w for w in p["warnings"])


def test_preview_sell_shows_realized_loss() -> None:
    b = make()
    b.place("TSLA", "buy", 10, "market")
    q = b.engine.quotes["TSLA"]
    p = b.preview("TSLA", "sell", 10, "market")
    assert p["position_after"] == 0
    assert p["estimated_realized"] == pytest.approx(10 * (q.bid - q.ask), abs=0.01)
    assert any("realizes a loss" in w for w in p["warnings"])


def test_preview_rejects_like_place() -> None:
    b = make()
    with pytest.raises(OrderError, match="Short selling"):
        b.preview("SPY", "sell", 1, "market")
    with pytest.raises(OrderError, match="Not enough buying power"):
        b.preview("SPY", "buy", 10_000, "market")


# ----- replace (cancel/replace) -------------------------------------------------


def test_replace_changes_price_and_links_orders() -> None:
    b = make()
    last = b.engine.quotes["SPY"].last
    old = b.place("SPY", "buy", 10, "limit", limit_price=round(last * 0.9, 2), tif="gtc")
    new = b.replace(old.id, limit_price=round(last * 0.8, 2), qty=20)
    assert old.status == "canceled" and old.reason == f"Replaced by #{new.id}"
    assert new.status == "working" and new.replaces == old.id
    assert (new.qty, new.limit_price, new.tif) == (20, round(last * 0.8, 2), "gtc")
    assert b.buying_power() == pytest.approx(STARTING_CASH - 20 * round(last * 0.8, 2))


def test_replace_can_use_its_own_reserved_cash() -> None:
    b = make()
    b.reset(10_000)
    o = b.place("AAPL", "buy", 40, "limit", limit_price=200.0)  # reserves $8,000 of $10,000
    new = b.replace(o.id, qty=45)  # needs $9,000: fine, since the old $8,000 is released
    assert new.qty == 45
    with pytest.raises(OrderError, match="Not enough buying power"):
        b.replace(new.id, qty=60)  # $12,000


def test_replace_sell_can_use_its_own_shares() -> None:
    b = make()
    b.place("AMD", "buy", 10, "market")
    o = b.place("AMD", "sell", 10, "limit", limit_price=9999)
    assert b.replace(o.id, limit_price=8888).qty == 10


def test_replace_that_becomes_marketable_fills() -> None:
    b = make()
    q = b.engine.quotes["QQQ"]
    o = b.place("QQQ", "buy", 1, "limit", limit_price=round(q.last * 0.9, 2))
    new = b.replace(o.id, limit_price=round(q.ask + 1, 2))
    assert new.status == "filled" and new.fill_price == q.ask


def test_replace_errors() -> None:
    b = make()
    o = b.place("SPY", "buy", 1, "limit", limit_price=1.0)
    with pytest.raises(OrderError, match="Nothing to change"):
        b.replace(o.id, limit_price=1.0)
    b.cancel(o.id)
    with pytest.raises(OrderError, match="already canceled"):
        b.replace(o.id, limit_price=2.0)
    with pytest.raises(OrderError, match="No order"):
        b.replace(999, qty=1)


def test_rewind_before_a_replace_restores_the_original() -> None:
    b = make()
    old = b.place("SPY", "buy", 1, "limit", limit_price=1.0, tif="gtc")
    move(b, et(2026, 1, 5, 11, 0))
    new = b.replace(old.id, limit_price=2.0)
    b.engine.rewind(et(2026, 1, 5, 10, 30), new_future=False)
    b.rewind(et(2026, 1, 5, 10, 30))
    assert new.id not in b.orders
    assert old.status == "working" and old.reason is None


def test_replaced_order_survives_reload(tmp_path) -> None:
    store = Store(tmp_path / "r.db")
    b = make(store=store)
    old = b.place("SPY", "buy", 1, "limit", limit_price=1.0)
    new = b.replace(old.id, limit_price=2.0)
    again = Broker(b.engine, Store(tmp_path / "r.db"))
    assert again.orders[new.id].replaces == old.id


def test_ids_are_never_reused_after_a_rewind() -> None:
    b = make()
    b.place("SPY", "buy", 1, "limit", limit_price=1.0)
    move(b, et(2026, 1, 5, 11, 0))
    erased = b.place("SPY", "buy", 1, "limit", limit_price=1.0)
    b.engine.rewind(et(2026, 1, 5, 10, 30), new_future=False)
    b.rewind(et(2026, 1, 5, 10, 30))
    assert erased.id not in b.orders
    assert b.place("SPY", "buy", 1, "limit", limit_price=1.0).id == erased.id + 1


def test_triggered_stop_limit_stays_triggered_when_modified() -> None:
    b = make()
    b.place("TSLA", "buy", 10, "market")
    last = b.engine.quotes["TSLA"].last
    # Stop already reached (triggers at once), limit far above: rests as a limit.
    o = b.place("TSLA", "sell", 10, "stop_limit", stop_price=round(last * 1.5, 2), limit_price=round(last * 2, 2), tif="gtc")
    assert o.triggered_at is not None and o.status == "working"
    new = b.replace(o.id, limit_price=round(last * 1.9, 2))
    assert new.triggered_at == o.triggered_at
    with pytest.raises(OrderError, match="stop already triggered"):
        b.replace(new.id, stop_price=round(last * 1.4, 2))


def test_preview_position_math_matches_the_ledger() -> None:
    b = make()
    b.place("AMD", "buy", 7, "market")
    move(b, et(2026, 1, 5, 12, 0))
    p = b.preview("AMD", "buy", 5, "market")
    b.place("AMD", "buy", 5, "market")
    pos = b.ledger().positions["AMD"]
    assert p["position_after"] == pos.qty
    assert p["avg_cost_after"] == pytest.approx(pos.avg_cost, abs=1e-4)
    assert p["cash_after"] == pytest.approx(b.ledger().cash, abs=0.01)
    p = b.preview("AMD", "sell", 12, "market")
    realized_before = pos.realized
    b.place("AMD", "sell", 12, "market")
    assert p["estimated_realized"] == pytest.approx(b.ledger().positions["AMD"].realized - realized_before, abs=0.01)


def test_day_warning_names_the_date() -> None:
    b = make()
    p = b.preview("SPY", "buy", 1, "limit", limit_price=1.0)
    assert any("today (Mon Jan 5)" in w for w in p["warnings"])
