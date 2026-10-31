from datetime import UTC, date, datetime

import pytest

from paper_trade.market.calendar import MARKET_TZ, session_close
from paper_trade.market.engine import MarketEngine
from paper_trade.store import Store
from paper_trade.trading.broker import Broker

START = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)
EX = date(2026, 3, 13)  # KO goes ex on this Friday; it pays three sessions later
PAY = date(2026, 3, 18)


def et(*args: int) -> datetime:
    return datetime(*args, tzinfo=MARKET_TZ)


def setup(at: datetime) -> tuple[MarketEngine, Broker]:
    e = MarketEngine(START)
    e.advance(at)
    b = Broker(e, Store(":memory:"))
    b.reset(100_000)
    return e, b


def advance(e: MarketEngine, b: Broker, t: datetime) -> None:
    t0 = e.time
    e.advance(t)
    b.process(t0, e.time)


def dividends(b: Broker) -> list:
    return [o for o in b.orders.values() if o.type == "dividend"]


def test_shares_held_at_the_ex_date_are_paid_three_sessions_later() -> None:
    e, b = setup(et(2026, 3, 10, 10, 0))
    b.place("KO", "buy", 100, "market")
    cash = b.ledger().cash
    advance(e, b, et(2026, 3, 17, 15, 0))
    assert dividends(b) == []  # not paid yet
    advance(e, b, et(2026, 3, 19, 10, 0))
    (d,) = dividends(b)
    amount = e.dividend(EX, e.symbol_index("KO"))
    assert amount > 0.1
    assert (d.qty, d.fill_price, d.filled_at) == (100, amount, session_close(PAY))
    assert b.ledger().cash == pytest.approx(cash + 100 * amount)
    assert b.ledger().qty("KO") == 100  # no change in shares


def test_a_buyer_on_the_ex_date_gets_nothing_and_a_seller_before_it_neither() -> None:
    e, b = setup(et(2026, 3, 10, 10, 0))
    b.place("KO", "buy", 100, "market")
    advance(e, b, et(2026, 3, 12, 15, 0))
    b.place("KO", "sell", 100, "market")
    advance(e, b, et(2026, 3, 13, 10, 0))
    b.place("KO", "buy", 50, "market")  # after the ex-date opens
    advance(e, b, et(2026, 3, 20, 10, 0))
    assert dividends(b) == []


def test_going_back_before_the_payment_removes_it_and_replaying_repeats_it() -> None:
    e, b = setup(et(2026, 3, 10, 10, 0))
    b.place("KO", "buy", 100, "market")
    advance(e, b, et(2026, 3, 20, 10, 0))
    (first,) = dividends(b)
    b.rewind(et(2026, 3, 16, 10, 0))
    assert dividends(b) == []
    e.rewind(et(2026, 3, 16, 10, 0), False)
    advance(e, b, et(2026, 3, 20, 10, 0))
    (again,) = dividends(b)
    assert again.fill_price == first.fill_price and again.qty == 100
