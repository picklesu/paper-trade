"""Runs the market and the account in the background and fans updates out to
subscribers. Everything here runs on the event loop's thread."""

import asyncio
import contextlib
import logging
from dataclasses import replace
from datetime import UTC, date, datetime
from typing import Literal

import numpy as np

from paper_trade.market.calendar import (
    SESSION_SECONDS,
    market_day,
    previous_trading_day,
    session_close,
    session_open,
    session_seconds,
    snap,
    trading_days_between,
)
from paper_trade.market.clock import SPEEDS, SimClock
from paper_trade.market.engine import HISTORY_DAYS, MarketEngine
from paper_trade.market.history import History
from paper_trade.market import options
from paper_trade.optiondata import OptionData
from paper_trade.macro import Macro
from paper_trade.pricehistory import PriceHistory, back_cast
from paper_trade.realprices import RealPrices
from paper_trade.store import Store
from paper_trade.trading.broker import Broker
from paper_trade.watchlists import Watchlists

TICK_SECONDS = 0.5
SAVE_EVERY_TICKS = 10  # save the market state about every 5 seconds while it runs
MAX_JUMP_DAYS = 252 * 25  # about 20 s of generation for 20 years
PROGRESS_EVERY_DAYS = 5
SPARKLINE_MINUTES = 5
SUPERSEDED = {"tick", "progress", "history"}  # messages a later one of the same kind replaces

log = logging.getLogger(__name__)

GotoAction = Literal["forward", "rewind", "reset", "none"]


class BusyError(Exception):
    """A time jump or the history build is running."""

    def __init__(self, message: str = "a time jump is already in progress") -> None:
        super().__init__(message)


class MarketService:
    def __init__(
        self,
        clock: SimClock,
        engine: MarketEngine,
        store: Store,
        history_days: int = HISTORY_DAYS,
        real: RealPrices | None = None,
        option_data: OptionData | None = None,
        price_history: PriceHistory | None = None,
        macro: Macro | None = None,
    ) -> None:
        self.clock = clock
        self.engine = engine
        self.store = store
        self.broker = Broker(engine, store)
        self.history = History(store.connection, engine)
        engine.on_day_complete = self.history.record
        # Anything saved after the market's last saved time never happened.
        self.broker.rewind(engine.time)
        self._subscribers: set[asyncio.Queue[dict]] = set()
        self._task: asyncio.Task | None = None
        self._jumping = False
        self._ticks_since_save = 0
        self._saved_starts_version: int | None = None
        self._history_task: asyncio.Task | None = None
        self._building = False
        # Bumped whenever market time jumps or history changes, so charts reload.
        self.timeline = 0
        self.history_days = history_days
        self.real = real
        self.watchlists = Watchlists(store, self.symbols + list(self.indexes))
        self._stats: tuple[tuple, dict] | None = None
        self._chains: dict[tuple[str, date], tuple[tuple, dict]] = {}
        self.option_data = option_data
        self.price_history = price_history
        self.macro = macro
        self.calibrations = options.calibrations(option_data.saved, options.RATE if option_data.rate is None else option_data.rate) if option_data else {}
        self._calibration_version = 0
        self.broker.calibration = self.calibration
        self.broker.listed = self.is_listed

    @property
    def jumping(self) -> bool:
        return self._jumping

    @property
    def building_history(self) -> bool:
        return self._building

    # ----- messages ----------------------------------------------------------------

    def listed_from(self, symbol: str) -> date | None:
        """The first day the symbol traded for real (None: unknown, always listed)."""
        return self.price_history.listed_from(symbol) if self.price_history else None

    def is_listed(self, symbol: str, day: date | None = None) -> bool:
        """Whether the symbol existed on `day` (default: the market's day)."""
        first = self.listed_from(symbol)
        return first is None or first <= (day or market_day(self.engine.time))

    def listed_quotes(self) -> list[dict]:
        """Quotes of symbols that exist now, then the indexes."""
        return [q.to_dict() for s, q in self.engine.quotes.items() if self.is_listed(s)] + self.index_quotes()

    def bars(self, symbol: str, interval: str, end: datetime | None, count: int) -> dict:
        """Chart bars, none from before the symbol was listed."""
        out = self.history.bars(symbol, interval, end, count)
        first = self.listed_from(symbol)
        if first is not None:
            start = session_open(first).timestamp()
            span = 7 * 86400 if interval == "1W" else 0  # a week's bar counts if the listing is in it
            kept = [b for b in out["bars"] if b[0] + span > start or b[0] == start]
            if len(kept) < len(out["bars"]):
                out = {"bars": kept, "more": False}
        return out

    def halts(self, start: datetime, end: datetime) -> list[dict]:
        """Circuit-breaker halts between two times, finished sessions and today's so far."""
        found = self.history.halts_between(market_day(start), market_day(end))
        today = market_day(self.engine.time)
        if market_day(start) <= today <= market_day(end):
            found += [h for h in self.engine.halts_of(today) if h["start"] <= self.engine.time.timestamp()]
        lo, hi = start.timestamp(), end.timestamp()
        return sorted({h["start"]: h for h in found if lo <= h["start"] <= hi}.values(), key=lambda h: h["start"])

    def snapshot(self) -> dict:
        quotes = self.engine.quotes
        return {
            "type": "tick",
            "clock": self.clock_state(),
            "regime": self.engine.regime,
            "shock": round(self.engine.shock_visible, 2),
            "shock_pending": self.engine.shock_pending,
            "halt": self.engine.halt,
            "quotes": self.listed_quotes(),
            "account": self.broker.summary(quotes),
        }

    @property
    def indexes(self) -> tuple[str, ...]:
        """Symbols that are quoted and charted but can't be traded."""
        return (options.VIX_SYMBOL,) if any(s == "SPY" for s in self.symbols) else ()

    def index_quotes(self) -> list[dict]:
        quote = self.engine.vix_quote() if self.indexes else None
        return [quote] if quote else []

    def quote_stats(self) -> dict[str, dict]:
        """52-week ranges and average volumes (cached until the day or the history changes)."""
        key = (market_day(self.engine.time), self.timeline)
        if self._stats is None or self._stats[0] != key:
            self._stats = (key, self.history.quote_stats())
        return self._stats[1]

    def sparklines(self, symbols: list[str]) -> dict:
        """Today's prices every SPARKLINE_MINUTES so far, ending with the latest."""
        closes = self.engine.day_closes(SPARKLINE_MINUTES)
        cols = {s: i for i, s in enumerate(self.symbols)}
        series = {s: [round(float(p), 2) for p in closes[:, cols[s]]] for s in symbols if s in cols}
        if options.VIX_SYMBOL in symbols and self.indexes:
            day, now = market_day(self.engine.time), int(session_seconds(self.engine.time))
            step = SPARKLINE_MINUTES * 60  # the same grid as the stocks: each stretch's last second, then now
            secs = np.append(np.arange(step - 1, now, step), now)
            series[options.VIX_SYMBOL] = [round(float(v), 2) for v in self.engine.vix_at(day, np.minimum(secs, SESSION_SECONDS - 1))]
        return {
            "step_minutes": SPARKLINE_MINUTES,
            "session_open": session_open(market_day(self.engine.time)).isoformat(),
            "series": series,
        }

    def calibration(self, symbol: str) -> options.Calibration:
        found = self.calibrations.get(symbol)
        if found is not None:
            return found
        rate = self.option_data.rate if self.option_data else None
        return replace(options.DEFAULT, rate=rate) if rate is not None else options.DEFAULT

    def option_expirations(self, symbol: str) -> list[dict]:
        today = market_day(self.engine.time)
        pattern = self.calibration(symbol).pattern
        listed = {e.day: e for e in options.expirations(symbol, self.engine.time, pattern)}
        # Expirations of contracts held stay listed (a new calibration's pattern may drop them).
        for c in self.broker.held_contracts(symbol):
            if c.expiry not in listed and session_close(c.expiry) > self.engine.time:
                listed[c.expiry] = options.Expiration(c.expiry, "weekly")
        return [listed[d].to_dict(today) for d in sorted(listed)]

    def option_chain(self, symbol: str, expiration: date) -> dict:
        """One expiration's chain now (computed once per market time and timeline,
        however many clients show it: a new future can come back to the same time
        with other prices, and a new calibration changes them). Raises ValueError
        for an unknown symbol or expiration."""
        if symbol not in self.symbols:
            raise ValueError(f"unknown symbol: {symbol}")
        if not self.is_listed(symbol):
            raise ValueError(f"{symbol} wasn't trading yet")
        held = frozenset(c.strike for c in self.broker.held_contracts(symbol) if c.expiry == expiration)
        key, now = (symbol, expiration), (self.engine.time, self.timeline, self._calibration_version, held)
        cached = self._chains.get(key)
        if cached is None or cached[0] != now:
            if len(self._chains) > 16:
                self._chains.clear()
            self._chains[key] = (now, options.chain(self.engine, symbol, expiration, self.calibration(symbol), set(held)))
        return self._chains[key][1]

    def watchlists_changed(self) -> None:
        self.publish(self.watchlists.message(), force=True)

    def orders_message(self) -> dict:
        orders = sorted(self.broker.orders.values(), key=lambda o: o.id, reverse=True)
        return {"type": "orders", "orders": [self.broker.order_dict(o) for o in orders]}

    def clock_state(self) -> dict:
        return {
            "time": self.engine.time.isoformat(),
            "speed": self.clock.speed,
            "paused": self.clock.paused,
            "speeds": SPEEDS,
            "origin": self.engine.origin.isoformat(),
            "session_open": session_open(market_day(self.engine.time)).isoformat(),
            "timeline": self.timeline,
            # Bumped when an option calibration changes (expirations may change too).
            "calibration": self._calibration_version,
            "building_history": self.building_history,
            # Sessions up to this day replay the real daily bars (None: all simulated).
            "real_through": self.engine.real_through.isoformat() if self.engine.real_through else None,
        }

    def publish(self, msg: dict, force: bool = False) -> None:
        """Send to every subscriber. A slow client just misses messages, since the
        next tick carries full state; with `force`, a queued tick makes room
        instead, for messages that must arrive."""
        for q in self._subscribers:
            self.send(q, msg, force)

    @staticmethod
    def send(q: asyncio.Queue[dict], msg: dict, force: bool = True) -> None:
        """Queue `msg` for one subscriber. When the queue is full and `force` is
        set, the oldest message a later one supersedes (a tick or progress) makes
        room, so `orders` and `watchlists` messages already queued still arrive."""
        if q.full() and force:
            held = [q.get_nowait() for _ in range(q.qsize())]
            stale = next((i for i, m in enumerate(held) if m["type"] in SUPERSEDED), 0)
            del held[stale]
            for m in held:
                q.put_nowait(m)
        with contextlib.suppress(asyncio.QueueFull):
            q.put_nowait(msg)

    def publish_all(self) -> None:
        """Full state that must reach every client: after orders or time change."""
        self.publish(self.orders_message(), force=True)
        self.publish(self.snapshot(), force=True)

    # ----- running -----------------------------------------------------------------

    def sync(self) -> None:
        """Bring the market and orders up to the clock. If any order filled,
        triggered or expired, save and tell every client, whoever called this."""
        if self._jumping:
            raise BusyError
        t0 = self.engine.time
        self.engine.advance(self.clock.now())
        if self.broker.process(t0, self.engine.time):
            self.save()
            self.publish(self.orders_message(), force=True)

    def tick(self) -> None:
        if self._jumping:
            return
        before = self.engine.time
        self.sync()
        # Nothing changes while paused; clock updates publish on their own.
        if self.engine.time != before:
            self.publish(self.snapshot())
            self._ticks_since_save += 1
            if self._ticks_since_save >= SAVE_EVERY_TICKS:
                self.save()

    def save(self) -> None:
        state = self.engine.to_state()
        # Session start states grow with history: write them only when they change.
        starts = state.pop("starts")
        if self.engine.starts_version != self._saved_starts_version:
            self.store.set("market_starts", starts)
            self._saved_starts_version = self.engine.starts_version
        self.store.set("market", state)
        self.store.set("clock", {"speed": self.clock.speed, "paused": self.clock.paused})
        self._ticks_since_save = 0

    # ----- time travel -------------------------------------------------------------

    def preview_goto(self, target: datetime) -> tuple[GotoAction, datetime]:
        """What going to `target` would do, and the market time it lands on."""
        target = snap(target)
        now = max(self.engine.time, snap(self.clock.now()))
        if target < self.engine.origin:
            return "reset", target
        if target < now:
            return "rewind", target
        if target > now:
            return "forward", target
        return "none", target

    def goto_details(self, action: GotoAction, target: datetime) -> dict:
        """For the confirmation: trades that would be erased and how holdings are revalued."""
        if action == "rewind":
            self.sync()
            # Recheck: syncing can move "now", but never past the target of a rewind preview.
            return self.broker.preview_rewind(min(target, self.engine.time))
        if action == "reset":
            fills = [o for o in self.broker.orders.values() if o.status == "filled"]
            return {
                "erased_fills": [self.broker.order_dict(o) for o in sorted(fills, key=lambda o: o.filled_at)],
                "erased_orders": len(self.broker.orders),
                "starting_cash": self.broker.starting_cash,
            }
        return {}

    async def goto(self, target: datetime, new_future: bool = False) -> tuple[GotoAction, datetime]:
        if self._jumping:
            raise BusyError
        if self.building_history:
            raise BusyError("price history is still being built; try again in a moment")
        action, target = self.preview_goto(target)
        # Only sessions not yet generated count: after a rewind, the way back is free.
        if action == "forward" and trading_days_between(self.engine.generated_through, market_day(target)) > MAX_JUMP_DAYS:
            raise ValueError(f"can jump at most {MAX_JUMP_DAYS} new trading days ahead")
        hist = self.price_history
        if action == "reset" and hist and hist.configured and not hist.fetched:
            # Prices of that time aren't downloaded yet: a market started now would open at today's prices.
            raise ValueError("real prices from the past aren't downloaded yet: run ./server.sh update history, then restart the server")
        if action == "rewind":
            # Match orders up to now first, so what gets rolled back is complete.
            self.sync()
            target = min(target, self.engine.time)

        self._jumping = True
        try:
            if action == "reset":
                self.engine.reset(target, self.real_start_prices(market_day(target)))
                self.broker.reset()
                self.history.clear()
            elif action == "rewind":
                shocks = set(self.engine._shocks)
                self.engine.rewind(target, new_future)
                if new_future or set(self.engine._shocks) != shocks:
                    self.history.delete_from(market_day(target))
                self.broker.rewind(target)
            elif action == "forward":
                await self._fast_forward(target)
            self.clock.set_time(target)
            self.save()
            self.timeline += 1
        finally:
            self._jumping = False
            if action == "reset":
                self.start_history()
            # Always end with full state (even after an error) so clients leave the
            # fast-forward state; never drop it for a full queue.
            self.publish_all()
        return action, target

    def trigger_shock(self) -> date:
        """Start an uncertainty shock at the next open. Later sessions that a replay
        had already generated are generated again, and what was recorded of them goes."""
        if self._jumping:
            raise BusyError
        if self.building_history:
            raise BusyError("price history is still being built; try again in a moment")
        self.sync()
        day = self.engine.trigger_shock()  # ValueError in the real past
        self.history.delete_from(day)
        self.save()
        self.publish_all()
        return day

    async def _fast_forward(self, target: datetime) -> None:
        # Sessions still to generate (fewer than the distance if a replay already generated some).
        days = trading_days_between(self.engine.generated_through, market_day(target))
        t = self.engine.time
        # Session by session: generate it, match working orders against it while its
        # price path is still cached, and yield so progress keeps flowing.
        with self.history.batch():
            for i, day in enumerate(self.engine.advance_iter(target), start=1):
                self.broker.process(t, session_open(day))
                t = session_open(day)
                if i % PROGRESS_EVERY_DAYS == 0:
                    self.publish({"type": "progress", "done": i, "total": days})
                await asyncio.sleep(0)
        self.broker.process(t, target)

    # ----- real prices -------------------------------------------------------------

    @property
    def symbols(self) -> list[str]:
        return [s.symbol for s in self.engine.specs]

    def real_start_prices(self, day: date | None = None) -> np.ndarray | None:
        """Previous closes for a new market starting on `day`: the real closes of the
        day before where they're saved, otherwise today's real price brought back by
        the symbol's expected return, otherwise the spec's price (None if there is
        nothing real at all)."""
        saved = self.real.saved if self.real else {}
        hist = self.price_history
        if not saved and not (hist and hist.fetched):
            return None
        before = previous_trading_day(day) if day else None
        prices = []
        for spec in self.engine.specs:
            price = hist.price_on(spec.symbol, before, spec.drift) if hist and before else None
            real = saved.get(spec.symbol)
            if real and before is not None and before >= real.as_of:
                price = real.price  # the saved real price is newer than any close fetched
            if price is None and real:
                price = real.price if before is None else back_cast(real.price, real.as_of, min(before, real.as_of), spec.drift)
            prices.append(spec.price if price is None else price)
        return np.array(prices)

    # ----- trading -----------------------------------------------------------------

    def after_trade(self) -> None:
        self.save()
        self.publish_all()

    # ----- lifecycle ---------------------------------------------------------------

    def subscribe(self) -> asyncio.Queue[dict]:
        q: asyncio.Queue[dict] = asyncio.Queue(maxsize=8)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict]) -> None:
        self._subscribers.discard(q)

    async def _run(self) -> None:
        while True:
            try:
                self.tick()
            except Exception:
                # Keep the market running; one bad tick shouldn't freeze every client.
                log.exception("market tick failed")
            await asyncio.sleep(TICK_SECONDS)

    def start_history(self) -> None:
        """Build missing chart history in the background (see build_history)."""
        self._building = True
        self._history_task = asyncio.create_task(self.build_history())

    async def build_history(self) -> None:
        """Store bars for finished sessions that have none, and generate the
        years of history before the market's start if not done yet."""
        try:
            backfill = 0 if self.engine.backfilled else self.history_days
            if backfill:
                # Bars before the origin are about to be generated again; any there now
                # (from a market continued from an older price model) no longer match.
                self.history.delete_before(market_day(self.engine.origin))
            missing = self.history.missing_days()
            total, done = len(missing) + backfill, 0
            with self.history.batch():
                for day in missing:
                    self.history.record(day)
                    done += 1
                    await self._history_progress(done, total)
                if backfill:
                    for _ in self.history.backfill_iter(backfill):
                        done += 1
                        await self._history_progress(done, total)
        except Exception:
            log.exception("building price history failed")
        finally:
            # Clear the flag before announcing, so the final snapshot says it's done.
            self._building = False
        self.save()
        self.timeline += 1
        self.publish_all()

    async def _history_progress(self, done: int, total: int) -> None:
        if done % PROGRESS_EVERY_DAYS == 0 or done == total:
            self.publish({"type": "history", "done": done, "total": total})
        await asyncio.sleep(0)

    def start(self) -> None:
        self.start_history()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._history_task:
            self._history_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._history_task
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self.save()
