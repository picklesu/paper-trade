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

Options (symbols are OCC symbols, e.g. AAPL261120C00230000; 100 shares a
contract): market and limit orders only. Their bid and ask come from
`options.price`, the same model the chain shows, at each second of the stock's
path. Buys open a position or close a short one; sells close a long one or
open a covered call (100 free shares a contract, locked while it's open) or a
cash-secured put (strike x 100 a contract set aside). At expiration (the 4:00 PM
close) options $0.01 or more in the money are exercised or assigned; others
expire worthless. A long option the account can't exercise (a call without the
cash, a put without the shares: that would be a short sale) is sold at its bid
at 3:59 PM instead. Short options deep in the money with little time value left
may be assigned early, at any close (a draw keyed to the market's path, so a
replay repeats it). Long options can be exercised early (`exercise`).

Cash account: no margin and no short selling. Buying power is cash minus
cash set aside for working buy orders and short puts; sells are limited to shares
owned and not already committed to other working sells or covered calls. A fill
may only use cash that isn't set aside for other orders; if it can't, the order
is rejected.
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta

import math

import numpy as np

from paper_trade.market import options
from paper_trade.market.calendar import (
    SESSION_SECONDS,
    add_trading_days,
    market_day,
    next_trading_day,
    previous_trading_day,
    session_close,
    session_open,
    session_seconds,
)
from paper_trade.market.engine import MarketEngine, Quote, _key, _unit_hash, bid_ask, bid_ask_arrays
from paper_trade.market.options import MULTIPLIER, Calibration, Contract
from paper_trade.store import Store
from paper_trade.trading.orders import EVENT_TYPES, Order, OrderType, Side, TimeInForce

STARTING_CASH = 100_000.0
MAX_QTY = 1_000_000
MAX_CONTRACTS = 10_000
DIVIDEND_ASSIGNMENT = 0.9  # chance a short call is assigned before a dividend its time value is less than
DIVIDEND_LAG = 3  # trading days from the ex-date to the payment (at that day's close)
MAX_PRICE = 1_000_000.0
IN_THE_MONEY = 0.01  # exercised or assigned at expiration from this much in the money
CLOSEOUT_SECOND = SESSION_SECONDS - 60  # 3:59 PM: unexercisable options are sold
# Early assignment: the chance at a close that a short in-the-money option is
# assigned, highest with no time value left, none from EARLY_TIME_VALUE up.
EARLY_ASSIGNMENT = 0.2
EARLY_TIME_VALUE = 0.05
MATCH_MARGIN = 0.01  # an option minute is looked at second by second if within 1% (and a cent)
# A limit inside an option's spread is the best price on its side, so it fills
# when someone trades with it: of the contract's trades (half on each side) a
# share that grows with how far into the spread it is, squared (none at the
# far edge, a quarter at the mid, all at the near one).
MINUTES_PER_SESSION = SESSION_SECONDS / 60
MIN_DAY_VOLUME = 40  # a quiet contract's trades a session, for fills inside the spread


class OrderError(ValueError):
    """An order request the account can't accept; the message is shown to the user."""


@dataclass
class Position:
    symbol: str
    qty: int = 0  # negative: short (options only)
    avg_cost: float = 0.0  # per share (an option's price per share; x100 a contract)
    realized: float = 0.0


@dataclass
class Ledger:
    cash: float
    positions: dict[str, Position] = field(default_factory=dict)
    # Realized P&L of each closing fill, by order id (opening fills realize nothing).
    realized_by_order: dict[int, float] = field(default_factory=dict)

    def qty(self, symbol: str) -> int:
        p = self.positions.get(symbol)
        return p.qty if p else 0


def multiplier(symbol: str) -> int:
    return MULTIPLIER if Contract.parse(symbol) else 1


def apply_fill(led: Ledger, symbol: str, side: str, qty: int, price: float) -> float | None:
    """Book a fill into a ledger: cash, quantity (signed), average cost and
    realized P&L. Returns the P&L a closing fill realizes (against the average
    cost), None for one that opens or adds."""
    p = led.positions.setdefault(symbol, Position(symbol))
    mult = multiplier(symbol)
    signed = qty if side == "buy" else -qty
    led.cash -= signed * price * mult
    if p.qty == 0 or (p.qty > 0) == (signed > 0):
        p.avg_cost = (p.avg_cost * abs(p.qty) + price * qty) / (abs(p.qty) + qty)
        p.qty += signed
        return None
    closed = min(qty, abs(p.qty))
    realized = (price - p.avg_cost) * closed * mult * (1 if p.qty > 0 else -1)
    p.realized += realized
    p.qty += signed
    if p.qty == 0:
        p.avg_cost = 0.0
    elif (p.qty > 0) == (signed > 0):  # crossed through zero (not allowed, but book it)
        p.avg_cost = price
    return realized


def money(x: float) -> str:
    return f"${x:,.2f}"


class Broker:
    def __init__(self, engine: MarketEngine, store: Store) -> None:
        self.engine = engine
        self.store = store
        self.orders: dict[int, Order] = {o.id: o for o in store.load_orders()}
        self.starting_cash: float = store.get("starting_cash", STARTING_CASH)
        # Each underlying's option calibration (the service sets it).
        self.calibration: Callable[[str], Calibration] = lambda symbol: options.DEFAULT
        # Whether a symbol existed on a day (the service sets it).
        self.listed: Callable[[str, date | None], bool] = lambda symbol, day=None: True
        self._ledger: Ledger | None = None
        self._day_start: tuple[date, Ledger, dict[str, float]] | None = None
        self._volumes: dict[tuple, float] = {}  # contracts expected to trade a session
        self._mid_fills: dict[tuple, np.ndarray] = {}  # seconds a limit inside the spread fills

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
            if o.type == "dividend":
                led.cash += o.qty * o.fill_price
                continue
            realized = apply_fill(led, o.symbol, o.side, o.qty, o.fill_price)
            if realized is not None:
                led.realized_by_order[o.id] = realized
        if before is None:
            self._ledger = led
        return led

    def order_dict(self, order: Order) -> dict:
        """An order as the API sends it: its stored fields, `realized` (the P&L a
        closing fill realized against the average cost; None otherwise; not
        rounded, so sums match the account's) and, for options, `option`."""
        out = {**order.to_dict(), "realized": self.ledger().realized_by_order.get(order.id)}
        if c := Contract.parse(order.symbol):
            out["option"] = _contract_dict(c)
        return out

    def working(self) -> list[Order]:
        return [o for o in self.orders.values() if o.status == "working"]

    def reserved_cash(self, excluding: Order | None = None) -> float:
        """Cash set aside for working orders: buys, and puts being sold to open."""
        return sum(self._reservation(o) for o in self.working() if o is not excluding)

    def collateral(self, led: Ledger | None = None) -> float:
        """Cash securing short puts: the strike x 100 for each."""
        led = led or self.ledger()
        total = 0.0
        for p in led.positions.values():
            c = Contract.parse(p.symbol)
            if c and c.right == "put" and p.qty < 0:
                total += c.strike * MULTIPLIER * -p.qty
        return total

    def reserved_shares(self, symbol: str, excluding: Order | None = None) -> int:
        return sum(o.qty for o in self.working() if o.side == "sell" and o.symbol == symbol and o is not excluding)

    def locked_shares(self, symbol: str, excluding: Order | None = None, led: Ledger | None = None) -> int:
        """Shares covering short calls on `symbol` and calls being sold to open."""
        led = led or self.ledger()
        locked = 0
        for p in led.positions.values():
            c = Contract.parse(p.symbol)
            if c and c.underlying == symbol and c.right == "call" and p.qty < 0:
                locked += -p.qty * MULTIPLIER
        for o in self.working():
            c = Contract.parse(o.symbol)
            if c and o is not excluding and c.underlying == symbol and c.right == "call" and o.side == "sell" and o.effect == "open":
                locked += o.qty * MULTIPLIER
        return locked

    def free_shares(self, symbol: str, excluding: Order | None = None) -> int:
        """Shares owned that no working sell or covered call has claimed."""
        led = self.ledger()
        return led.qty(symbol) - self.reserved_shares(symbol, excluding) - self.locked_shares(symbol, excluding, led)

    def buying_power(self) -> float:
        return self.ledger().cash - self.reserved_cash() - self.collateral()

    def held_contracts(self, underlying: str | None = None) -> list[Contract]:
        """Option contracts held (long or short)."""
        out = []
        for p in self.ledger().positions.values():
            c = Contract.parse(p.symbol)
            if c and p.qty and (underlying is None or c.underlying == underlying):
                out.append(c)
        return out

    def option_quote(self, c: Contract) -> dict:
        """A contract's quote now: bid, ask, mid and Greeks (per share)."""
        sec = min(int(session_seconds(self.engine.time)), SESSION_SECONDS - 1)
        return self._quote_at(c, market_day(self.engine.time), sec, self.engine.quotes[c.underlying].last)

    def summary(self, quotes: dict[str, Quote]) -> dict:
        led = self.ledger()
        positions = []
        for p in led.positions.values():
            if p.qty == 0:
                continue
            c = Contract.parse(p.symbol)
            mult = MULTIPLIER if c else 1
            q = self.option_quote(c) if c else None
            last = q["mid"] if q else quotes[p.symbol].last
            cost = p.avg_cost * p.qty * mult
            value = last * p.qty * mult
            row = {
                "symbol": p.symbol,
                "qty": p.qty,
                "avg_cost": round(p.avg_cost, 4),
                "last": round(last, 2),
                "market_value": round(value, 2),
                "unrealized": round(value - cost, 2),
                "unrealized_pct": round((value - cost) / abs(cost) * 100, 2) if cost else 0.0,
                # Share-equivalent delta and dollars a day of theta.
                "delta": round(q["delta"] * p.qty * mult, 2) if q else p.qty,
                "theta": round(q["theta"] * p.qty * mult, 2) if q else 0.0,
            }
            if c:
                row["option"] = _contract_dict(c)
                row["bid"], row["ask"] = round(q["bid"], 2), round(q["ask"], 2)
            positions.append(row)
        net_liq = led.cash + sum(p["market_value"] for p in positions)
        return {
            "starting_cash": self.starting_cash,
            "cash": round(led.cash, 2),
            "buying_power": round(self.buying_power(), 2),
            "collateral": round(self.collateral(led), 2),
            "net_liq": round(net_liq, 2),
            "day_pnl": round(net_liq - self._start_of_day_value(quotes), 2),
            "realized": round(sum(p.realized for p in led.positions.values()), 2),
            "positions": positions,
        }

    def _start_of_day_value(self, quotes: dict[str, Quote]) -> float:
        """Account value at the previous close: holdings before today's open at
        the previous close (options at their mid then)."""
        day = market_day(self.engine.time)
        if self._day_start is None or self._day_start[0] != day:
            led = self.ledger(before=session_open(day))
            mids = {}
            for p in led.positions.values():
                if p.qty and (c := Contract.parse(p.symbol)):
                    mids[p.symbol] = self._mid_at_close(c, previous_trading_day(day))
            self._day_start = (day, led, mids)
        _, led, mids = self._day_start
        total = led.cash
        for p in led.positions.values():
            if not p.qty:
                continue
            if p.symbol in mids:
                total += p.qty * MULTIPLIER * mids[p.symbol]
            else:
                total += p.qty * quotes[p.symbol].prev_close
        return total

    def _mid_at_close(self, c: Contract, day: date) -> float:
        """A contract's mid at a session's close (its intrinsic value if it had
        expired, or if that session's prices aren't available)."""
        col = self.engine.symbol_index(c.underlying)
        try:
            spot = self.engine.price_at(day, SESSION_SECONDS - 1, col)
        except KeyError:
            return float(c.intrinsic(self.engine.quotes[c.underlying].prev_close))
        if c.expiry < day:
            return float(c.intrinsic(spot))
        return self._quote_at(c, day, SESSION_SECONDS - 1, spot)["mid"]

    def _quote_at(self, c: Contract, day: date, sec: int, spot: float) -> dict:
        p = options.price(self.engine, c.underlying, c.expiry, c.strike, day, sec, spot, self.calibration(c.underlying))
        q = {k: float(v[0, 0]) for k, v in getattr(p, c.right).items()}
        return q | {"mid": (q["bid"] + q["ask"]) / 2}

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
        c = Contract.parse(o.symbol)
        mult = MULTIPLIER if c else 1
        if c:
            q = self.option_quote(c)
            last, bid, ask = q["mid"], q["bid"], q["ask"]
            triggered, fill = False, _option_fill(o, q)
        else:
            sq = self.engine.quotes[symbol]
            last, bid, ask = sq.last, sq.bid, sq.ask
            triggered, fill = self._immediate(o, sq.last, self.engine.spread_factor(self.engine.time))
        if fill is not None:
            est = fill
        elif o.has_limit:
            est = o.limit_price
        elif side == "buy":  # a stop fills at the market once triggered
            est = max(o.stop_price, ask)
        else:
            est = min(o.stop_price, bid)
        total = est * qty * mult

        led = self.ledger()
        pos = led.positions.get(o.symbol, Position(o.symbol))
        # The position as it would be after the fill, booked by the same rules as real fills.
        after = Ledger(led.cash, {o.symbol: replace(pos)})
        apply_fill(after, o.symbol, side, qty, est)
        pos_after = after.positions[o.symbol]
        closing = o.effect == "close" if c else o.side == "sell"
        realized = pos_after.realized - pos.realized if closing else None
        bp = self.buying_power()
        if fill is not None:
            # Cash moves now; selling a put to open also sets the strike aside.
            bp_after = bp + after.cash - led.cash - self._collateral_of(o) + self._freed(o)
        else:
            bp_after = bp - self._reservation(o)

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
        inside = c is not None and o.type == "limit" and bid < o.limit_price < ask  # can fill at any time
        if o.has_limit and not inside and last > 0 and abs(o.limit_price - last) / last > (0.25 if c else 0.10):
            pct = abs(o.limit_price - last) / last * 100
            what = "the mid" if c else "the last price"
            warnings.append(f"The limit is {pct:.0f}% away from {what} ({last:,.2f}), so it may take a long time to fill.")
        if fill is None and tif == "day" and type != "market":
            day = market_day(self.engine.time).strftime("%a %b %-d")
            warnings.append(f"Day order: if it hasn't filled by 4:00 PM ET today ({day}), it expires.")
        if realized is not None and realized < 0:
            warnings.append(f"{'Selling' if side == 'sell' else 'Buying back'} at about {est:,.2f} realizes a loss of about {money(-realized)}.")
        out = {
            **{k: v for k, v in o.to_dict().items() if k in ("symbol", "side", "qty", "type", "tif", "limit_price", "stop_price")},
            "fills_now": fill is not None,
            "estimated_price": round(est, 2),
            "estimated_total": round(total, 2),
            "commission": 0.0,
            "buying_power_before": round(bp, 2),
            "buying_power_after": round(bp_after, 2),
            "cash_before": round(led.cash, 2),
            "cash_after": round(after.cash, 2),
            "position_before": pos.qty,
            "position_after": pos_after.qty,
            "avg_cost_after": round(pos_after.avg_cost, 4),
            "estimated_realized": None if realized is None else round(realized, 2),
            "warnings": warnings,
        }
        if c:
            out |= self._option_preview(o, c, est, warnings, q)
        return out

    def _option_preview(self, o: Order, c: Contract, est: float, warnings: list[str], q: dict) -> dict:
        """What's particular to an option order: what it opens or closes, cash or
        shares it ties up, and (opening) its breakeven and worst case."""
        n = o.qty * MULTIPLIER
        call = c.right == "call"
        breakeven = max_loss = max_gain = None
        if o.effect == "open":
            breakeven = round(c.strike + est if call else c.strike - est, 2)
            if o.side == "buy":
                max_loss = round(est * n, 2)
            else:
                max_gain = round(est * n, 2)
                if not call:
                    max_loss = round((c.strike - est) * n, 2)
        if o.side == "sell" and o.effect == "open":
            warnings.append(
                f"Covered call: {n:,} shares of {c.underlying} are locked until it's closed, expires or is assigned "
                f"(if the price is above {c.strike:g} at expiration, they're sold at {c.strike:g})."
                if call
                else f"Cash-secured put: {money(c.strike * n)} is set aside; if the price is below {c.strike:g} "
                f"at expiration, you buy {n:,} shares at {c.strike:g}."
            )
        if o.type == "limit" and q["bid"] < o.limit_price < q["ask"]:
            rate = float(self._mid_fill_rate(o, c, np.array([q["bid"]]), np.array([q["ask"]]), market_day(self.engine.time))[0])
            warnings.append(
                f"Your limit is between the bid ({q['bid']:.2f}) and the ask ({q['ask']:.2f}): it fills if someone trades "
                f"with it before the quote reaches it, {_wait(rate)} at today's activity "
                f"(sooner the nearer it is to the {'ask' if o.side == 'buy' else 'bid'}, and for busier contracts)."
            )
        days = (c.expiry - market_day(self.engine.time)).days
        if days <= 1 and o.effect == "open":
            warnings.append("This option expires " + ("today" if days == 0 else "tomorrow") + " at 4:00 PM ET.")
        return {
            "option": _contract_dict(c),
            "effect": o.effect,
            "multiplier": MULTIPLIER,
            "breakeven": breakeven,
            "max_loss": max_loss,
            "max_gain": max_gain,
            "collateral": round(self._collateral_of(o), 2),
            "shares_locked": n if (call and o.side == "sell" and o.effect == "open") else 0,
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
        c = Contract.parse(symbol)
        if symbol == options.VIX_SYMBOL:
            raise OrderError("VIX is an index: it can't be traded")
        if c is None and symbol not in self.engine.quotes:
            raise OrderError(f"Unknown symbol {symbol}")
        if c and c.underlying not in self.engine.quotes:
            raise OrderError(f"Unknown symbol {c.underlying}")
        underlying = c.underlying if c else symbol
        if not self.listed(underlying, None):
            raise OrderError(f"{underlying} wasn't trading yet")
        if type not in ("market", "limit", "stop", "stop_limit"):
            raise OrderError(f"Unknown order type {type}")
        if c and type not in ("market", "limit"):
            raise OrderError("Options take market and limit orders only")
        most = MAX_CONTRACTS if c else MAX_QTY
        if not 1 <= qty <= most:
            raise OrderError(f"Quantity must be between 1 and {most:,}")
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
        if c:
            self._check_option(order, c, replacing)
        elif side == "buy":
            self._check_cash(order, replacing)
        else:
            owned = self.ledger().qty(symbol)
            if owned == 0:
                raise OrderError(f"You don't own any {symbol}. Short selling isn't allowed in a cash account")
            free = self.free_shares(symbol, excluding=replacing)
            if qty > free:
                locked = self.locked_shares(symbol, excluding=replacing)
                held = f", {locked:,} covering calls" if locked else ""
                raise OrderError(
                    f"You can sell at most {max(free, 0):,} shares of {symbol} ({owned:,} owned, "
                    f"{self.reserved_shares(symbol, excluding=replacing):,} in other sell orders{held})"
                )
        return order

    def _check_cash(self, order: Order, replacing: Order | None) -> None:
        need = self._reservation(order)
        available = self.ledger().cash - self.reserved_cash(excluding=replacing) - self.collateral() + self._freed(order)
        if need > available + 1e-9:
            raise OrderError(f"Not enough buying power: this order needs {money(need)}, you have {money(available)}")

    def _check_option(self, o: Order, c: Contract, replacing: Order | None) -> None:
        """Options: the contract must be open for trading; the order opens or
        closes (never both); opening a short needs shares or cash behind it."""
        now = self.engine.time
        if session_close(c.expiry) <= now:
            raise OrderError(f"{c.label} has expired")
        if c.strike <= 0 or round(c.strike * 2) != c.strike * 2:
            raise OrderError(f"{c.label} isn't a listed strike")
        held = self.ledger().qty(o.symbol)
        if not held:
            listed = {e.day for e in options.expirations(c.underlying, now, self.calibration(c.underlying).pattern)}
            if c.expiry not in listed:
                raise OrderError(f"{c.underlying} has no {c.expiry:%b %-d, %Y} expiration")
        pending = sum(w.qty for w in self.working() if w.symbol == o.symbol and w.side == o.side and w.effect == "close" and w is not replacing)
        if o.side == "buy" and held < 0:
            if o.qty > -held - pending:
                raise OrderError(f"You're short {-held:,} {c.label}; buy at most {max(-held - pending, 0):,} to close ({pending:,} in other orders)")
            o.effect = "close"
        elif o.side == "sell" and held > 0:
            if o.qty > held - pending:
                raise OrderError(f"You hold {held:,} {c.label}; sell at most {max(held - pending, 0):,} to close ({pending:,} in other orders)")
            o.effect = "close"
        else:
            o.effect = "open"
        if o.side == "buy":
            self._check_cash(o, replacing)
        elif o.effect == "close":
            return
        elif c.right == "call":
            free = self.free_shares(c.underlying, excluding=replacing)
            if o.qty * MULTIPLIER > free:
                raise OrderError(
                    f"Selling a call to open needs {MULTIPLIER} shares of {c.underlying} a contract (a covered call; "
                    f"a cash account can't sell uncovered calls): {max(free, 0):,} shares are free"
                )
        else:
            self._check_cash(o, replacing)

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

    def _freed(self, o: Order) -> float:
        """Collateral a put bought to close frees once filled."""
        c = Contract.parse(o.symbol)
        if c and c.right == "put" and o.side == "buy" and o.effect == "close":
            return c.strike * MULTIPLIER * o.qty
        return 0.0

    def _collateral_of(self, o: Order) -> float:
        """Cash a put sold to open sets aside once filled."""
        c = Contract.parse(o.symbol)
        if c and c.right == "put" and o.side == "sell" and o.effect == "open":
            return c.strike * MULTIPLIER * o.qty
        return 0.0

    def _reservation(self, o: Order) -> float:
        """Cash set aside for a working order: a buy's limit (for a stop, the
        higher of the stop and the current ask, as it fills at the ask once
        triggered; for a market order, the ask), or a put sold to open's strike."""
        if o.side == "sell":
            return self._collateral_of(o)
        c = Contract.parse(o.symbol)
        if o.has_limit:
            price = o.limit_price
        elif c:
            price = self.option_quote(c)["ask"]
        elif o.type == "stop":
            price = max(o.stop_price, self.engine.quotes[o.symbol].ask)
        else:
            price = self.engine.quotes[o.symbol].ask
        return price * o.qty * (MULTIPLIER if c else 1)

    # ----- exercise ----------------------------------------------------------------

    def preview_exercise(self, symbol: str, qty: int) -> dict:
        """What exercising long contracts now would do. Raises OrderError if it can't."""
        c, qty = self._check_exercise(symbol, qty)
        q = self.option_quote(c)
        spot = self.engine.quotes[c.underlying].last
        intrinsic = float(c.intrinsic(spot))
        n = qty * MULTIPLIER
        cash = -c.strike * n if c.right == "call" else c.strike * n
        warnings = []
        lost = (q["bid"] - intrinsic) * n
        if lost > 0.5:
            warnings.append(f"Selling the contracts at the bid ({q['bid']:,.2f}) would get about {money(lost)} more than exercising.")
        if intrinsic <= 0:
            warnings.append(f"{c.label} is out of the money: exercising it {'buys' if c.right == 'call' else 'sells'} shares at a worse price than the market's.")
        led = self.ledger()
        return {
            "symbol": c.symbol,
            "option": _contract_dict(c),
            "qty": qty,
            "shares": n if c.right == "call" else -n,
            "strike": c.strike,
            "cash_change": round(cash, 2),
            "cash_before": round(led.cash, 2),
            "cash_after": round(led.cash + cash, 2),
            "intrinsic": round(intrinsic, 2),
            "bid": round(q["bid"], 2),
            "warnings": warnings,
        }

    def exercise(self, symbol: str, qty: int) -> list[Order]:
        """Exercise long contracts now: shares bought (call) or sold (put) at the strike."""
        c, qty = self._check_exercise(symbol, qty)
        t = self.engine.time
        spot = round(self.engine.quotes[c.underlying].last, 2)
        out = self._settle(c, qty, "exercise", spot, t, "Exercised early")
        self._invalidate()
        return out

    def _check_exercise(self, symbol: str, qty: int) -> tuple[Contract, int]:
        c = Contract.parse(symbol)
        if c is None:
            raise OrderError(f"{symbol} isn't an option")
        held = self.ledger().qty(symbol)
        pending = sum(o.qty for o in self.working() if o.symbol == symbol and o.side == "sell")
        if held <= 0:
            raise OrderError(f"You don't hold any {c.label} to exercise")
        if not 1 <= qty <= held - pending:
            raise OrderError(f"You can exercise 1 to {max(held - pending, 0):,} contracts ({pending:,} in sell orders)")
        if session_close(c.expiry) <= self.engine.time:
            raise OrderError(f"{c.label} has expired")
        n = qty * MULTIPLIER
        if c.right == "call":
            need = c.strike * n
            if need > self.buying_power() + 1e-9:
                raise OrderError(f"Exercising needs {money(need)} to buy {n:,} shares at {c.strike:g}; buying power is {money(self.buying_power())}")
        elif n > self.free_shares(c.underlying):
            raise OrderError(f"Exercising a put sells {n:,} shares of {c.underlying}; {max(self.free_shares(c.underlying), 0):,} are free (a cash account can't sell short)")
        return c, qty

    def _settle(self, c: Contract, qty: int, kind: OrderType, spot: float, t: datetime, reason: str) -> list[Order]:
        """Exercise (long) or assignment (short) of `qty` contracts at stock price
        `spot`: the option closes at its intrinsic value and the shares trade at
        `spot`, so the cash nets to the strike."""
        long = kind == "exercise"
        option = self._record(c.symbol, "sell" if long else "buy", qty, float(c.intrinsic(spot)), t, kind, reason)
        buys_shares = (c.right == "call") == long
        stock = self._record(c.underlying, "buy" if buys_shares else "sell", qty * MULTIPLIER, spot, t, kind, f"{reason}: {c.label}")
        return [option, stock]

    def _record(self, symbol: str, side: Side, qty: int, price: float, t: datetime, kind: OrderType, reason: str | None) -> Order:
        o = Order(
            id=self._next_id(),
            symbol=symbol,
            side=side,
            qty=qty,
            type=kind,
            tif="day",
            limit_price=None,
            stop_price=None,
            created_at=t,
            status="filled",
            filled_at=t,
            fill_price=round(price, 2),
            reason=reason,
        )
        if Contract.parse(symbol):
            o.effect = "close"
        self.orders[o.id] = o
        self._save(o)
        self._invalidate()
        return o

    # ----- matching --------------------------------------------------------------

    def process(self, t0: datetime, t1: datetime) -> bool:
        """Match working orders against prices after `t0` up to `t1`. True if any changed."""
        changed = False
        for changed_today in self.process_iter(t0, t1):
            changed |= changed_today
        return changed

    def _busy(self, day: date) -> bool:
        """Anything to do at the close after `day`: working orders, option positions,
        shares held, or shares traded since an ex-date whose dividend isn't paid yet."""
        if self.working() or self.held_contracts():
            return True
        if any(p.qty > 0 and not Contract.parse(p.symbol) for p in self.ledger().positions.values()):
            return True
        since = session_open(add_trading_days(day, 1 - DIVIDEND_LAG))  # the next close pays the ex-date this far back
        return any(
            o.status == "filled" and o.type not in EVENT_TYPES and not Contract.parse(o.symbol) and o.filled_at >= since
            for o in self.orders.values()
        )

    def process_iter(self, t0: datetime, t1: datetime) -> Iterator[bool]:
        """Like process(), yielding after each session (True if orders changed)."""
        if t1 <= t0 or not self._busy(market_day(t0)):
            return
        day, end_day = market_day(t0), market_day(t1)
        first = int(session_seconds(t0)) + 1
        while True:
            last = int(session_seconds(t1)) if day == end_day else SESSION_SECONDS - 1
            changed = self._process_day(day, first, last)
            if day == end_day:
                yield changed
                return
            # Day orders (and orders on contracts expiring now) go first: the cash and
            # shares they held are free for exercises at this close.
            changed |= self._expire_day_orders(day)
            changed |= self._settle_options(day)
            changed |= self._pay_dividends(day)
            yield changed
            if not self._busy(day):
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
                c = Contract.parse(o.symbol)
                k = self._next_option_event(o, c, day, start, hi) if c else self._next_event(o, day, start, hi)
                if k is not None and (best is None or (k, o.id) < (best[0], best[1].id)):
                    best = (k, o)
            if best is None:
                return changed
            k, o = best
            t = session_open(day) + timedelta(seconds=k)
            c = Contract.parse(o.symbol)
            if c:
                fill = o.limit_price
                if k == 0:  # gapped through at the open: the better price
                    spot = self.engine.price_at(day, 0, self.engine.symbol_index(c.underlying))
                    q = self._quote_at(c, day, 0, spot)
                    fill = min(fill, q["ask"]) if o.side == "buy" else max(fill, q["bid"])
                self._fill(o, fill, t)
                self._save(o)
                changed = True
                continue
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

    def _mid_fill_rate(self, o: Order, c: Contract, bid, ask, day: date):
        """Chance a minute's trading fills a limit between the bid and ask (rate
        per minute, an array like `bid`); 0 outside the spread."""
        width = np.maximum(ask - bid, 1e-9)
        x = (o.limit_price - bid) / width if o.side == "buy" else (ask - o.limit_price) / width
        inside = (bid < o.limit_price) & (o.limit_price < ask)
        # Keyed by the session's path too: a new future (or a reset) has other prices.
        key = (c.symbol, day, self.engine.option_inputs(day, self.engine.symbol_index(c.underlying)).minute_seed)
        if key not in self._volumes:
            if len(self._volumes) > 256:
                self._volumes.clear()
            self._volumes[key] = options.expected_volume(self.engine, c, day, self.calibration(c.underlying))
        # Trades on the order's other side; market makers trade even quiet contracts now and then.
        per_minute = max(self._volumes[key], MIN_DAY_VOLUME) / MINUTES_PER_SESSION / 2
        return np.where(inside, per_minute * np.clip(x, 0, 1) ** 2, 0.0)

    def _mid_fill_seconds(self, o: Order, c: Contract, day: date, inputs, cal) -> np.ndarray:
        """The seconds of `day` at which a limit inside the spread is traded with,
        one chance a minute (quoted at its first second): a draw keyed to the
        market's path and the order picks whether, and which second, so the
        answer is the same however finely time is processed, and a replay
        repeats it. Worked out once per order and session."""
        key = (o.id, o.limit_price, day, inputs.minute_seed)
        if key not in self._mid_fills:
            if len(self._mid_fills) > 1024:
                self._mid_fills.clear()
            minutes = np.arange(len(inputs.opens))
            p = options.price(self.engine, c.underlying, c.expiry, c.strike, day, 60 * minutes, inputs.opens, cal, inputs)
            side = getattr(p, c.right)
            chance = -np.expm1(-self._mid_fill_rate(o, c, side["bid"][:, 0], side["ask"][:, 0], day))
            seeds = (inputs.minute_seeds.astype(np.uint64) ^ np.uint64(_key(f"mid:{o.id}"))) % np.uint64(2**64 - 1)
            u = _unit_hash(seeds)
            hit = u < chance
            at = np.minimum(59, (u / np.where(hit, chance, 1) * 60).astype(int))
            self._mid_fills[key] = (60 * minutes + at)[hit]
        return self._mid_fills[key]

    def _next_mid_fill(self, o: Order, c: Contract, day: date, lo: int, hi: int, inputs, cal) -> int | None:
        """First second in [lo, hi] at which a limit inside the spread is traded with."""
        seconds = self._mid_fill_seconds(o, c, day, inputs, cal)
        i = int(np.searchsorted(seconds, lo))
        return int(seconds[i]) if i < len(seconds) and seconds[i] <= hi else None

    def _next_option_event(self, o: Order, c: Contract, day: date, lo: int, hi: int) -> int | None:
        """First second in [lo, hi] at which an option limit fills: the model's
        ask for a buy (bid for a sell) reaches it, or, inside the spread, someone
        trades with it (`_next_mid_fill`). At a fixed moment an option's price
        moves one way with the stock, so each minute is first priced at its stock
        extreme favoring the order, at both ends of the minute (time moves the
        price too), with a small margin; only minutes that come close are priced
        second by second."""
        if lo > hi or day > c.expiry:
            return None
        col = self.engine.symbol_index(c.underlying)
        cal = self.calibration(c.underlying)
        inputs = self.engine.option_inputs(day, col)
        mid = self._next_mid_fill(o, c, day, lo, hi, inputs, cal)
        if mid is not None:
            hi = mid  # a touch before it comes first
        high, low = self.engine.minute_range(day, col)
        buy, limit = o.side == "buy", o.limit_price
        best = low if buy == (c.right == "call") else high  # cheaper to buy / dearer to sell
        field_ = "ask" if buy else "bid"
        minutes = np.arange(lo // 60, hi // 60 + 1)
        secs = np.concatenate([np.maximum(60 * minutes, lo), np.minimum(60 * minutes + 59, hi)])
        p = options.price(self.engine, c.underlying, c.expiry, c.strike, day, secs, np.tile(best[minutes], 2), cal, inputs)
        q = getattr(p, c.right)[field_][:, 0].reshape(2, -1)
        if buy:
            near = q.min(axis=0) <= limit * (1 + MATCH_MARGIN) + 0.01
        else:
            near = q.max(axis=0) >= limit * (1 - MATCH_MARGIN) - 0.01
        for m in minutes[near]:
            a, b = max(lo, 60 * m), min(hi, 60 * m + 59)
            spots = self.engine.minute_prices(day, int(m), col)[a - 60 * m : b - 60 * m + 1]
            p = options.price(self.engine, c.underlying, c.expiry, c.strike, day, np.arange(a, b + 1), spots, cal, inputs)
            q = getattr(p, c.right)[field_][:, 0]
            hits = np.flatnonzero(q <= limit + 1e-9 if buy else q >= limit - 1e-9)
            if hits.size:
                return int(a + hits[0])
        return mid

    def _activate_now(self, o: Order) -> None:
        """An order placed now goes live at the current quote."""
        t = self.engine.time
        c = Contract.parse(o.symbol)
        if c:
            if (fill := _option_fill(o, self.option_quote(c))) is not None:
                self._fill(o, fill, t)
            return
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
        c = Contract.parse(o.symbol)
        mult = MULTIPLIER if c else 1
        if o.side == "buy" or self._collateral_of(o):
            # Cash set aside for other working orders and short puts isn't available to this one.
            available = led.cash - self.reserved_cash(excluding=o) - self.collateral(led) + self._freed(o)
            need = price * o.qty * mult if o.side == "buy" else self._collateral_of(o)
            if need > available + 1e-9:
                self._close(o, "rejected", t, f"Not enough cash at fill: needed {money(need)}, had {money(available)}")
                return
        held = led.qty(o.symbol)
        if c and o.effect == "close" and o.qty > (held if o.side == "sell" else -held):
            self._close(o, "rejected", t, "The position to close isn't there any more")
            return
        if c and o.effect == "open" and held and (held > 0) != (o.side == "buy"):
            # Placed to open, but the position is now the other way: it would close instead.
            self._close(o, "rejected", t, "The position changed since this order was placed (it would now close); place it again")
            return
        if c and o.side == "sell" and o.effect == "open" and c.right == "call":
            if o.qty * MULTIPLIER > self.free_shares(c.underlying, excluding=o):
                self._close(o, "rejected", t, f"Not enough free shares of {c.underlying} to cover the call at fill")
                return
        if not c and o.side == "sell" and o.qty > held - self.locked_shares(o.symbol, led=led):
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
            c = Contract.parse(o.symbol)
            if (o.tif == "day" and market_day(o.created_at) <= day) or (c and c.expiry <= day):
                self._close(o, "expired", session_close(day))
                self._save(o)
                changed = True
        return changed

    # ----- expiration and assignment ---------------------------------------------

    def _settle_options(self, day: date) -> bool:
        """At `day`'s close: contracts expiring that day are exercised, assigned or
        expire; short options with little time value left may be assigned early."""
        held = self.held_contracts()
        if not held:
            return False
        before = len(self.orders)
        expiring = sorted((c for c in held if c.expiry <= day), key=lambda c: (self.ledger().qty(c.symbol) > 0, c.symbol))
        for c in expiring:  # shorts first: assignments use cash and shares already set aside
            self._expire(c, day)
        for c in held:
            if c.expiry > day:
                self._maybe_assign_early(c, day)
        return len(self.orders) != before

    def _pay_dividends(self, day: date) -> bool:
        """At `day`'s close: dividends that went ex DIVIDEND_LAG sessions ago are paid
        on the shares held when that session opened."""
        ex = add_trading_days(day, -DIVIDEND_LAG)
        if not any(self.engine.dividend_schedule(i, ex, ex) for i in range(len(self.engine.specs))):
            return False
        held = self.ledger(before=session_open(ex))
        paid = session_close(day)
        before = len(self.orders)
        for symbol, p in sorted(held.positions.items()):
            if p.qty <= 0 or Contract.parse(symbol):
                continue
            amount = self.engine.dividend(ex, self.engine.symbol_index(symbol))
            if amount <= 0 or any(o.type == "dividend" and o.symbol == symbol and o.filled_at == paid for o in self.orders.values()):
                continue
            self._record(symbol, "buy", p.qty, amount, paid, "dividend", f"Dividend, ex-date {ex.isoformat()}")
        return len(self.orders) != before

    def _close_price(self, c: Contract, day: date) -> float:
        return round(self.engine.price_at(day, SESSION_SECONDS - 1, self.engine.symbol_index(c.underlying)), 2)

    def _expire(self, c: Contract, day: date) -> None:
        qty = self.ledger().qty(c.symbol)
        close = session_close(day)
        spot = self._close_price(c, day)
        if c.intrinsic(spot) < IN_THE_MONEY:
            self._record(c.symbol, "sell" if qty > 0 else "buy", abs(qty), 0.0, close, "expiration", "Expired worthless")
            return
        if qty < 0:
            self._settle(c, -qty, "assignment", spot, close, "Assigned at expiration")
            return
        n = c.strike * MULTIPLIER
        if c.right == "call":
            available = self.ledger().cash - self.reserved_cash() - self.collateral()
            can = min(qty, max(0, int(available // n)))
            why = "not enough cash to buy the shares"
        else:
            can = min(qty, max(0, self.free_shares(c.underlying)) // MULTIPLIER)
            why = "no shares to deliver (exercising would sell short)"
        if can:
            self._settle(c, can, "exercise", spot, close, "Exercised at expiration")
        if rest := qty - can:
            t = session_open(day) + timedelta(seconds=CLOSEOUT_SECOND)
            col = self.engine.symbol_index(c.underlying)
            bid = self._quote_at(c, day, CLOSEOUT_SECOND, self.engine.price_at(day, CLOSEOUT_SECOND, col))["bid"]
            self._record(c.symbol, "sell", rest, bid, t, "market", f"Sold before expiration: {why}")

    def _maybe_assign_early(self, c: Contract, day: date) -> None:
        qty = self.ledger().qty(c.symbol)
        if qty >= 0:
            return
        spot = self._close_price(c, day)
        intrinsic = float(c.intrinsic(spot))
        if intrinsic < IN_THE_MONEY:
            return
        time_value = max(self._quote_at(c, day, SESSION_SECONDS - 1, spot)["mid"] - intrinsic, 0.0)
        # The holder of a call gets the dividend by exercising the day before the ex-date,
        # which pays when the call's time value is less than the dividend.
        chance, reason = EARLY_ASSIGNMENT * max(0.0, 1 - time_value / EARLY_TIME_VALUE), "Assigned early"
        dividend = self._dividend_next_session(c, day, spot)
        if c.right == "call" and dividend > time_value:
            chance, reason = max(chance, DIVIDEND_ASSIGNMENT * (1 - time_value / dividend)), f"Assigned before the {money(dividend)} dividend"
        if chance <= 0:
            return
        seed = self.engine.option_inputs(day, self.engine.symbol_index(c.underlying)).minute_seed
        u = float(_unit_hash(np.array([(seed ^ _key(f"assign:{c.symbol}")) % 2**64], dtype=np.uint64))[0])
        if u < chance:
            self._settle(c, -qty, "assignment", spot, session_close(day), reason)

    def _dividend_next_session(self, c: Contract, day: date, spot: float) -> float:
        """The dividend per share the underlying pays at the next session's open (0 if none).
        The next session opens at today's close, so it is a fraction of that price."""
        nxt = next_trading_day(day)
        schedule = self.engine.dividend_schedule(self.engine.symbol_index(c.underlying), nxt, nxt)
        return round(sum(f for _, f in schedule) * spot, 2)

    # ----- time travel -----------------------------------------------------------

    def preview_rewind(self, t: datetime) -> dict:
        """What going back to `t` would erase, and how holdings would be valued then
        (options with today's calibration)."""
        erased = sorted(
            (o for o in self.orders.values() if o.status == "filled" and o.filled_at > t),
            key=lambda o: o.filled_at,
        )
        then = self.ledger(before=t + timedelta(microseconds=1))
        prices_then = self.engine.prices_at(t)
        quotes_now = self.engine.quotes
        day, sec = market_day(t), min(int(session_seconds(t)), SESSION_SECONDS - 1)
        positions = []
        for p in then.positions.values():
            if not p.qty:
                continue
            if c := Contract.parse(p.symbol):
                if c.expiry < day or session_close(c.expiry) <= t:
                    continue
                mid_then = self._quote_at(c, day, sec, prices_then[c.underlying])["mid"]
                now = self.option_quote(c)["mid"] if session_close(c.expiry) > self.engine.time else float(c.intrinsic(quotes_now[c.underlying].last))
                positions.append({"symbol": p.symbol, "qty": p.qty, "option": _contract_dict(c), "price_now": round(now, 2), "price_then": round(mid_then, 2)})
                continue
            positions.append(
                {
                    "symbol": p.symbol,
                    "qty": p.qty,
                    "price_now": round(quotes_now[p.symbol].last, 2),
                    "price_then": round(prices_then[p.symbol], 2),
                }
            )
        return {
            "erased_fills": [self.order_dict(o) for o in erased],
            "erased_orders": sum(1 for o in self.orders.values() if o.created_at > t),
            "cash_now": round(self.ledger().cash, 2),
            "cash_then": round(then.cash, 2),
            "positions": positions,
        }

    def rewind(self, t: datetime) -> None:
        """Undo everything after `t`: later orders (and exercises, assignments and
        expirations) are erased, and fills, cancels, expirations and stop triggers
        after `t` are rolled back."""
        for o in list(self.orders.values()):
            if o.created_at > t or (o.type in EVENT_TYPES and o.filled_at and o.filled_at > t):
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


def _wait(rate: float) -> str:
    """A typical wait, given a chance per minute."""
    if rate <= 0:
        return "which may not happen"
    minutes = 1 / rate
    if minutes < 2:
        return "likely within a minute or two"
    if minutes < 90:
        return f"typically in about {minutes:.0f} minutes"
    hours = minutes / 60
    if hours < 6.5 * 3:
        return f"typically in about {hours:.0f} hours of trading"
    return f"typically in about {hours / 6.5:.0f} trading days"


def _option_fill(o: Order, q: dict) -> float | None:
    """The price an option order trades at now against quote `q`, if it does."""
    market = q["ask"] if o.side == "buy" else q["bid"]
    if o.type == "market" or (market <= o.limit_price if o.side == "buy" else market >= o.limit_price):
        return market
    return None


def _contract_dict(c: Contract) -> dict:
    return {"underlying": c.underlying, "expiration": c.expiry.isoformat(), "right": c.right, "strike": c.strike, "label": c.label}


def _after(t: datetime | None, day: date) -> int:
    """First second of `day` strictly after `t` (0 if `t` was on an earlier day or unset)."""
    if t is None or market_day(t) < day:
        return 0
    return int(session_seconds(t)) + 1
