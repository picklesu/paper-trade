"""A cash brokerage account: order entry, matching against the market's
per-second price path, and the account (cash, positions, P&L) rebuilt from fills.

Fill rules:
- Market orders fill at once, buys at the ask and sells at the bid.
- When an order becomes active (placed, or a stop-limit's stop is hit) and its
  limit is already marketable, it fills at the ask/bid, like a market order.
- A resting limit fills at its limit price once the quote reaches it (the ask
  for a buy, the bid for a sell), or at the better opening price if the market
  gaps through it overnight.
- A stop triggers when the last price reaches the stop price. A stop then
  fills at the ask/bid; a stop-limit becomes a limit order.
- Day orders expire at the 4:00 PM close of the day they were placed.

Cash account: no margin and no short selling. Buying power is cash minus
cash set aside for working buy orders; sells are limited to shares owned and
not already committed to other working sells. A fill may only use cash that
isn't set aside for other orders; if it can't, the order is rejected.
"""

from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta

import math

import numpy as np

from paper_trade.market.calendar import SESSION_SECONDS, market_day, next_trading_day, session_close, session_open, session_seconds
from paper_trade.market.engine import MarketEngine, Quote, bid_ask, bid_ask_arrays
from paper_trade.store import Store
from paper_trade.trading.orders import Order, OrderType, Side, TimeInForce

STARTING_CASH = 100_000.0
MAX_QTY = 1_000_000
MAX_PRICE = 1_000_000.0


class OrderError(ValueError):
    """An order request the account can't accept; the message is shown to the user."""


@dataclass
class Position:
    symbol: str
    qty: int = 0
    avg_cost: float = 0.0
    realized: float = 0.0


@dataclass
class Ledger:
    cash: float
    positions: dict[str, Position] = field(default_factory=dict)
    # Realized P&L of each filled sell, by order id (a buy realizes nothing).
    realized_by_order: dict[int, float] = field(default_factory=dict)


def apply_fill(led: Ledger, symbol: str, side: str, qty: int, price: float) -> float | None:
    """Book a fill into a ledger: cash, quantity, average cost and realized P&L. Returns the
    P&L a sell realizes (against the average cost), None for a buy."""
    p = led.positions.setdefault(symbol, Position(symbol))
    amount = price * qty
    if side == "buy":
        led.cash -= amount
        p.avg_cost = (p.avg_cost * p.qty + amount) / (p.qty + qty)
        p.qty += qty
        return None
    led.cash += amount
    realized = (price - p.avg_cost) * qty
    p.realized += realized
    p.qty -= qty
    if p.qty == 0:
        p.avg_cost = 0.0
    return realized


def money(x: float) -> str:
    return f"${x:,.2f}"


class Broker:
    def __init__(self, engine: MarketEngine, store: Store) -> None:
        self.engine = engine
        self.store = store
        self.orders: dict[int, Order] = {o.id: o for o in store.load_orders()}
        self.starting_cash: float = store.get("starting_cash", STARTING_CASH)
        self._ledger: Ledger | None = None
        self._day_start: tuple[date, Ledger] | None = None

    def _invalidate(self) -> None:
        """Fills changed: rebuild the ledger and the start-of-day snapshot on next use."""
        self._ledger = None
        self._day_start = None

    # ----- account -------------------------------------------------------------

    def ledger(self, before: datetime | None = None) -> Ledger:
        """Cash and positions from all fills (or only fills before `before`)."""
        if before is None and self._ledger is not None:
            return self._ledger
        led = Ledger(self.starting_cash)
        fills = sorted(
            (o for o in self.orders.values() if o.status == "filled"), key=lambda o: (o.filled_at, o.id)
        )
        for o in fills:
            if before is not None and o.filled_at >= before:
                break
            realized = apply_fill(led, o.symbol, o.side, o.qty, o.fill_price)
            if realized is not None:
                led.realized_by_order[o.id] = realized
        if before is None:
            self._ledger = led
        return led

    def order_dict(self, order: Order) -> dict:
        """An order as the API sends it: its stored fields plus `realized`, the P&L a filled sell
        realized against the average cost (None for buys and unfilled orders). Not rounded, so
        sums of them match the account's realized P&L; the frontend rounds for display."""
        return {**order.to_dict(), "realized": self.ledger().realized_by_order.get(order.id)}

    def working(self) -> list[Order]:
        return [o for o in self.orders.values() if o.status == "working"]

    def reserved_cash(self, excluding: Order | None = None) -> float:
        return sum(self._reservation(o) for o in self.working() if o.side == "buy" and o is not excluding)

    def reserved_shares(self, symbol: str, excluding: Order | None = None) -> int:
        return sum(o.qty for o in self.working() if o.side == "sell" and o.symbol == symbol and o is not excluding)

    def buying_power(self) -> float:
        return self.ledger().cash - self.reserved_cash()

    def summary(self, quotes: dict[str, Quote]) -> dict:
        led = self.ledger()
        positions = []
        for p in led.positions.values():
            if p.qty == 0:
                continue
            last = quotes[p.symbol].last
            cost = p.avg_cost * p.qty
            value = last * p.qty
            positions.append(
                {
                    "symbol": p.symbol,
                    "qty": p.qty,
                    "avg_cost": round(p.avg_cost, 4),
                    "last": round(last, 2),
                    "market_value": round(value, 2),
                    "unrealized": round(value - cost, 2),
                    "unrealized_pct": round((value - cost) / cost * 100, 2),
                }
            )
        net_liq = led.cash + sum(p["market_value"] for p in positions)
        return {
            "starting_cash": self.starting_cash,
            "cash": round(led.cash, 2),
            "buying_power": round(self.buying_power(), 2),
            "net_liq": round(net_liq, 2),
            "day_pnl": round(net_liq - self._start_of_day_value(quotes), 2),
            "realized": round(sum(p.realized for p in led.positions.values()), 2),
            "positions": positions,
        }

    def _start_of_day_value(self, quotes: dict[str, Quote]) -> float:
        """Account value at the previous close: holdings before today's open at prev_close."""
        day = market_day(self.engine.time)
        if self._day_start is None or self._day_start[0] != day:
            self._day_start = (day, self.ledger(before=session_open(day)))
        led = self._day_start[1]
        return led.cash + sum(p.qty * quotes[p.symbol].prev_close for p in led.positions.values())

    # ----- order entry -----------------------------------------------------------

    def preview(
        self,
        symbol: str,
        side: Side,
        qty: int,
        type: OrderType,
        tif: TimeInForce = "day",
        limit_price: float | None = None,
        stop_price: float | None = None,
    ) -> dict:
        """What placing this order would do, without placing it. Raises OrderError
        exactly as place() would."""
        o = self._build(symbol, side, qty, type, tif, limit_price, stop_price)
        q = self.engine.quotes[symbol]
        triggered, fill = self._immediate(o, q.last, self.engine.spread_factor(self.engine.time))
        if fill is not None:
            est = fill
        elif o.has_limit:
            est = o.limit_price
        elif side == "buy":  # a stop fills at the market once triggered
            est = max(o.stop_price, q.ask)
        else:
            est = min(o.stop_price, q.bid)
        total = est * qty

        led = self.ledger()
        pos = led.positions.get(symbol, Position(symbol))
        # The position as it would be after the fill, booked by the same rules as real fills.
        after = Ledger(led.cash, {symbol: replace(pos)})
        apply_fill(after, symbol, side, qty, est)
        pos_after = after.positions[symbol]
        bp = self.buying_power()
        if side == "buy":
            bp_after = bp - (total if fill is not None else self._reservation(o))
            realized = None
        else:
            bp_after = bp + (total if fill is not None else 0)
            realized = pos_after.realized - pos.realized
        qty_after, avg_after, cash_after = pos_after.qty, pos_after.avg_cost, after.cash

        warnings = []
        if triggered:
            warnings.append("The stop price is already reached, so the order triggers right away.")
        if fill is not None and type != "market":
            warnings.append(f"This order can trade now, so it fills right away at about {est:,.2f}.")
        if type in ("stop", "stop_limit") and not triggered:
            warnings.append(
                "A stop fills at the market once triggered; if prices jump past the stop, the fill can be worse."
                if type == "stop"
                else "Once the stop triggers, the order only fills at the limit price or better, and may not fill."
            )
        if o.has_limit and abs(o.limit_price - q.last) / q.last > 0.10:
            pct = abs(o.limit_price - q.last) / q.last * 100
            warnings.append(f"The limit is {pct:.0f}% away from the last price ({q.last:,.2f}), so it may take a long time to fill.")
        if fill is None and tif == "day" and type != "market":
            day = market_day(self.engine.time).strftime("%a %b %-d")
            warnings.append(f"Day order: if it hasn't filled by 4:00 PM ET today ({day}), it expires.")
        if side == "sell" and realized is not None and realized < 0:
            warnings.append(f"Selling at about {est:,.2f} realizes a loss of about {money(-realized)}.")

        return {
            **{k: v for k, v in o.to_dict().items() if k in ("symbol", "side", "qty", "type", "tif", "limit_price", "stop_price")},
            "fills_now": fill is not None,
            "estimated_price": round(est, 2),
            "estimated_total": round(total, 2),
            "commission": 0.0,
            "buying_power_before": round(bp, 2),
            "buying_power_after": round(bp_after, 2),
            "cash_before": round(led.cash, 2),
            "cash_after": round(cash_after, 2),
            "position_before": pos.qty,
            "position_after": qty_after,
            "avg_cost_after": round(avg_after, 4),
            "estimated_realized": None if realized is None else round(realized, 2),
            "warnings": warnings,
        }

    def place(
        self,
        symbol: str,
        side: Side,
        qty: int,
        type: OrderType,
        tif: TimeInForce = "day",
        limit_price: float | None = None,
        stop_price: float | None = None,
    ) -> Order:
        order = self._build(symbol, side, qty, type, tif, limit_price, stop_price)
        self.orders[order.id] = order
        self._activate_now(order)
        self._save(order)
        return order

    def replace(
        self,
        order_id: int,
        qty: int | None = None,
        limit_price: float | None = None,
        stop_price: float | None = None,
        tif: TimeInForce | None = None,
    ) -> Order:
        """Change a working order: cancel it and place a new one with the changes
        (as brokers do). The new order records which one it replaces."""
        old = self._working_order(order_id)
        if old.triggered_at is not None and stop_price is not None and round(stop_price, 2) != old.stop_price:
            raise OrderError("The stop already triggered, so this order is working as a limit: change the limit instead")
        new = self._build(
            old.symbol,
            old.side,
            old.qty if qty is None else qty,
            old.type,
            tif or old.tif,
            old.limit_price if limit_price is None else limit_price,
            old.stop_price if stop_price is None else stop_price,
            replacing=old,
        )
        if (new.qty, new.tif, new.limit_price, new.stop_price) == (old.qty, old.tif, old.limit_price, old.stop_price):
            raise OrderError("Nothing to change")
        new.replaces = old.id
        new.triggered_at = old.triggered_at  # a stop-limit whose stop already hit stays triggered
        self._close(old, "canceled", self.engine.time, f"Replaced by #{new.id}")
        self._save(old)
        self.orders[new.id] = new
        self._activate_now(new)
        self._save(new)
        return new

    def _build(
        self,
        symbol: str,
        side: Side,
        qty: int,
        type: OrderType,
        tif: TimeInForce,
        limit_price: float | None,
        stop_price: float | None,
        replacing: Order | None = None,
    ) -> Order:
        """Validate an order request and return the order, not yet placed. When
        `replacing`, that order's reserved cash or shares count as available."""
        quotes = self.engine.quotes
        if symbol not in quotes:
            raise OrderError(f"Unknown symbol {symbol}")
        if not 1 <= qty <= MAX_QTY:
            raise OrderError(f"Quantity must be between 1 and {MAX_QTY:,}")
        if type == "market":
            limit_price = stop_price = None
            tif = "day"
        if type in ("limit", "stop_limit"):
            limit_price = self._price_input(limit_price, "Limit price")
        else:
            limit_price = None
        if type in ("stop", "stop_limit"):
            stop_price = self._price_input(stop_price, "Stop price")
        else:
            stop_price = None

        order = Order(
            id=self._next_id(),
            symbol=symbol,
            side=side,
            qty=qty,
            type=type,
            tif=tif,
            limit_price=limit_price,
            stop_price=stop_price,
            created_at=self.engine.time,
        )

        if side == "buy":
            need = self._reservation(order)
            available = self.ledger().cash - self.reserved_cash(excluding=replacing)
            if need > available:
                raise OrderError(f"Not enough buying power: this order needs {money(need)}, you have {money(available)}")
        else:
            owned = self.ledger().positions.get(symbol, Position(symbol)).qty
            free = owned - self.reserved_shares(symbol, excluding=replacing)
            if owned == 0:
                raise OrderError(f"You don't own any {symbol}. Short selling isn't allowed in a cash account")
            if qty > free:
                raise OrderError(f"You can sell at most {free:,} shares of {symbol} ({owned:,} owned, {owned - free:,} in other sell orders)")
        return order

    def _next_id(self) -> int:
        """Ids are never reused, even after going back in time erases orders."""
        return max(self.store.get("last_order_id", 0), max(self.orders, default=0)) + 1

    def _working_order(self, order_id: int) -> Order:
        order = self.orders.get(order_id)
        if order is None:
            raise OrderError(f"No order #{order_id}")
        if order.status != "working":
            raise OrderError(f"Order #{order_id} is already {order.status}")
        return order

    def cancel(self, order_id: int) -> Order:
        order = self._working_order(order_id)
        order.status = "canceled"
        order.closed_at = self.engine.time
        self._save(order)
        return order

    @staticmethod
    def _price_input(value: float | None, label: str) -> float:
        if value is None or not math.isfinite(value) or not 0 < value <= MAX_PRICE:
            raise OrderError(f"{label} must be between $0 and {money(MAX_PRICE)}")
        return round(value, 2)

    def _reservation(self, o: Order) -> float:
        """Cash set aside for a working buy order: its limit, or for a stop the
        higher of the stop and the current ask (it fills at the ask once triggered)."""
        if o.has_limit:
            price = o.limit_price
        elif o.type == "stop":
            price = max(o.stop_price, self.engine.quotes[o.symbol].ask)
        else:
            price = self.engine.quotes[o.symbol].ask
        return price * o.qty

    # ----- matching --------------------------------------------------------------

    def process(self, t0: datetime, t1: datetime) -> bool:
        """Match working orders against prices after `t0` up to `t1`. True if any changed."""
        changed = False
        for changed_today in self.process_iter(t0, t1):
            changed |= changed_today
        return changed

    def process_iter(self, t0: datetime, t1: datetime) -> Iterator[bool]:
        """Like process(), yielding after each session (True if orders changed)."""
        if t1 <= t0 or not self.working():
            return
        day, end_day = market_day(t0), market_day(t1)
        first = int(session_seconds(t0)) + 1
        while True:
            last = int(session_seconds(t1)) if day == end_day else SESSION_SECONDS - 1
            changed = self._process_day(day, first, last)
            if day == end_day:
                yield changed
                return
            changed |= self._expire_day_orders(day)
            yield changed
            if not self.working():
                return
            day, first = next_trading_day(day), 0

    def _process_day(self, day: date, lo: int, hi: int) -> bool:
        if lo > hi:
            return False
        changed = False
        while True:
            # Handle events in time order: one order's fill can affect another's (cash, shares).
            best: tuple[int, Order] | None = None
            for o in self.working():
                start = max(lo, _after(o.created_at, day), _after(o.triggered_at, day))
                k = self._next_event(o, day, start, hi)
                if k is not None and (best is None or (k, o.id) < (best[0], best[1].id)):
                    best = (k, o)
            if best is None:
                return changed
            k, o = best
            t = session_open(day) + timedelta(seconds=k)
            price = self.engine.price_at(day, k, self.engine.symbol_index(o.symbol))
            spread = self.engine.spread_factor(t)
            if o.waiting_for_stop:
                self._activate(o, t, price, spread)  # the stop is reached at t: trigger it
            else:
                # A resting limit fills at its price, or better if the open gapped through it.
                fill = o.limit_price
                if k == 0:
                    bid, ask = bid_ask(price, spread)
                    fill = min(fill, ask) if o.side == "buy" else max(fill, bid)
                self._fill(o, fill, t)
            self._save(o)
            changed = True

    def _next_event(self, o: Order, day: date, lo: int, hi: int) -> int | None:
        """First second in [lo, hi] at which the order's stop triggers (on the last
        price) or its limit is reached (by the ask for a buy, the bid for a sell).
        Only minutes whose range reaches the price are looked at second by second."""
        if lo > hi:
            return None
        col = self.engine.symbol_index(o.symbol)
        high, low = self.engine.minute_range(day, col)
        buy = o.side == "buy"
        if o.waiting_for_stop:
            stop = o.stop_price
            candidates = high >= stop if buy else low <= stop

            def test(p: np.ndarray, spread: float) -> np.ndarray:
                return p >= stop if buy else p <= stop
        else:
            limit = o.limit_price
            # The ask is at or above the price and the bid at or below it.
            candidates = low <= limit if buy else high >= limit

            def test(p: np.ndarray, spread: float) -> np.ndarray:
                bid, ask = bid_ask_arrays(p, spread)
                return ask <= limit if buy else bid >= limit

        return self.engine.first_hit(day, col, lo, hi, candidates, test)

    def _activate_now(self, o: Order) -> None:
        """An order placed now goes live at the current quote."""
        t = self.engine.time
        self._activate(o, t, self.engine.quotes[o.symbol].last, self.engine.spread_factor(t))

    def _activate(self, o: Order, t: datetime, price: float, spread: float) -> None:
        """An order goes live at `t` with the last price `price`: fill it if it's marketable now."""
        triggered, fill = self._immediate(o, price, spread)
        if triggered:
            o.triggered_at = t
        if fill is not None:
            self._fill(o, fill, t)

    @staticmethod
    def _immediate(o: Order, price: float, spread: float) -> tuple[bool, float | None]:
        """What happens to `o` at once with the last price `price` (and spreads
        `spread` times normal), without changing anything: whether its stop
        triggers, and the fill price if it trades now."""
        bid, ask = bid_ask(price, spread)
        market_price = ask if o.side == "buy" else bid
        if o.type == "market":
            return False, market_price
        triggered = False
        if o.waiting_for_stop:
            reached = price >= o.stop_price if o.side == "buy" else price <= o.stop_price
            if not reached:
                return False, None
            triggered = True
            if o.type == "stop":
                return True, market_price
        marketable = market_price <= o.limit_price if o.side == "buy" else market_price >= o.limit_price
        return triggered, market_price if marketable else None

    def _fill(self, o: Order, price: float, t: datetime) -> None:
        led = self.ledger()
        if o.side == "buy":
            # Cash set aside for other working buys isn't available to this one.
            available = led.cash - self.reserved_cash(excluding=o)
            if price * o.qty > available + 1e-9:
                self._close(o, "rejected", t, f"Not enough cash at fill: needed {money(price * o.qty)}, had {money(available)}")
                return
        if o.side == "sell" and o.qty > led.positions.get(o.symbol, Position(o.symbol)).qty:
            self._close(o, "rejected", t, "Not enough shares at fill")
            return
        o.status = "filled"
        o.fill_price = round(price, 2)
        o.filled_at = t
        self._invalidate()

    def _close(self, o: Order, status: str, t: datetime, reason: str | None = None) -> None:
        o.status = status
        o.closed_at = t
        o.reason = reason

    def _expire_day_orders(self, day: date) -> bool:
        changed = False
        for o in self.working():
            if o.tif == "day" and market_day(o.created_at) <= day:
                self._close(o, "expired", session_close(day))
                self._save(o)
                changed = True
        return changed

    # ----- time travel -----------------------------------------------------------

    def preview_rewind(self, t: datetime) -> dict:
        """What going back to `t` would erase, and how holdings would be valued then."""
        erased = sorted(
            (o for o in self.orders.values() if o.status == "filled" and o.filled_at > t),
            key=lambda o: o.filled_at,
        )
        then = self.ledger(before=t + timedelta(microseconds=1))
        prices_then = self.engine.prices_at(t)
        quotes_now = self.engine.quotes
        return {
            "erased_fills": [self.order_dict(o) for o in erased],
            "erased_orders": sum(1 for o in self.orders.values() if o.created_at > t),
            "cash_now": round(self.ledger().cash, 2),
            "cash_then": round(then.cash, 2),
            "positions": [
                {
                    "symbol": p.symbol,
                    "qty": p.qty,
                    "price_now": round(quotes_now[p.symbol].last, 2),
                    "price_then": round(prices_then[p.symbol], 2),
                }
                for p in then.positions.values()
                if p.qty
            ],
        }

    def rewind(self, t: datetime) -> None:
        """Undo everything after `t`: later orders are erased, and fills,
        cancels, expirations and stop triggers after `t` are rolled back."""
        for o in list(self.orders.values()):
            if o.created_at > t:
                del self.orders[o.id]
                self.store.delete_order(o.id)
                continue
            dirty = False
            if o.filled_at and o.filled_at > t:
                o.status, o.filled_at, o.fill_price = "working", None, None
                dirty = True
            if o.closed_at and o.closed_at > t:
                o.status, o.closed_at, o.reason = "working", None, None
                dirty = True
            if o.triggered_at and o.triggered_at > t:
                o.triggered_at = None
                dirty = True
            if dirty:
                self.store.save_order(o)
        self._invalidate()

    def reset(self, starting_cash: float | None = None) -> None:
        """Erase all orders and start over with fresh cash."""
        if starting_cash is not None:
            if not 0 < starting_cash <= 1e9:
                raise OrderError("Starting cash must be between $0 and $1,000,000,000")
            self.starting_cash = round(starting_cash, 2)
            self.store.set("starting_cash", self.starting_cash)
        self.orders.clear()
        self.store.delete_all_orders()
        self._invalidate()

    def _save(self, o: Order) -> None:
        self.store.save_order(o)
        if o.id > self.store.get("last_order_id", 0):
            self.store.set("last_order_id", o.id)


def _after(t: datetime | None, day: date) -> int:
    """First second of `day` strictly after `t` (0 if `t` was on an earlier day or unset)."""
    if t is None or market_day(t) < day:
        return 0
    return int(session_seconds(t)) + 1
