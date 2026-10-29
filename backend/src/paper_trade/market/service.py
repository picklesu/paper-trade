"""Runs the market and the account in the background and fans updates out to
subscribers. Everything here runs on the event loop's thread."""

import asyncio
import contextlib
import logging
from datetime import datetime, timedelta
from typing import Literal

import numpy as np

from paper_trade.market.calendar import market_day, session_open, snap, trading_days_between
from paper_trade.market.clock import SPEEDS, SimClock
from paper_trade.market.engine import HISTORY_DAYS, MarketEngine
from paper_trade.market.history import History
from paper_trade.realprices import REFRESH_EVERY, STATUS_REFRESH_AFTER, RealPrices
from paper_trade.store import Store
from paper_trade.trading.broker import Broker

TICK_SECONDS = 0.5
SAVE_EVERY_TICKS = 10  # save the market state about every 5 seconds while it runs
MAX_JUMP_DAYS = 252 * 5
PROGRESS_EVERY_DAYS = 5

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
        self._real_task: asyncio.Task | None = None
        self._real_lock = asyncio.Lock()

    @property
    def jumping(self) -> bool:
        return self._jumping

    @property
    def building_history(self) -> bool:
        return self._building

    # ----- messages ----------------------------------------------------------------

    def snapshot(self) -> dict:
        quotes = self.engine.quotes
        return {
            "type": "tick",
            "clock": self.clock_state(),
            "regime": self.engine.regime,
            "quotes": [q.to_dict() for q in quotes.values()],
            "account": self.broker.summary(quotes),
        }

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
            "building_history": self.building_history,
        }

    def publish(self, msg: dict, force: bool = False) -> None:
        """Send to every subscriber. A slow client just misses messages, since the
        next tick carries full state; with `force`, its oldest message is dropped
        instead, for messages that must arrive."""
        for q in self._subscribers:
            if force and q.full():
                q.get_nowait()
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
        if action == "forward" and trading_days_between(market_day(self.engine.time), market_day(target)) > MAX_JUMP_DAYS:
            raise ValueError(f"can jump at most {MAX_JUMP_DAYS} trading days ahead")
        if action == "rewind":
            # Match orders up to now first, so what gets rolled back is complete.
            self.sync()
            target = min(target, self.engine.time)

        self._jumping = True
        try:
            if action == "reset":
                self.engine.reset(target, self.real_start_prices())
                self.broker.reset()
                self.history.clear()
            elif action == "rewind":
                self.engine.rewind(target, new_future)
                if new_future:
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

    def real_prices_status(self) -> dict:
        if self.real is None:
            return {"configured": False, "available": 0, "total": len(self.symbols)}
        return self.real.status(self.symbols)

    def real_start_prices(self) -> np.ndarray | None:
        """Previous closes for a new market: saved real prices where there are
        any, the specs' elsewhere (None if there are none at all)."""
        saved = self.real.saved if self.real else {}
        if not saved:
            return None
        return np.array([saved[s.symbol].price if s.symbol in saved else s.price for s in self.engine.specs])

    async def sync_real_prices(self) -> dict:
        """Continue the market from now as a new one whose prices are the saved
        real ones (symbols without one keep their current price). The account
        and orders are kept; the history is generated again to end here."""
        if self._jumping:
            raise BusyError
        if self.building_history:
            raise BusyError("price history is still being built; try again in a moment")
        saved = self.real.saved if self.real else {}
        if not saved:
            raise ValueError("no real prices have been fetched yet")
        self.sync()
        quotes = self.engine.quotes
        prices = np.array([saved[s].price if s in saved else quotes[s].last for s in self.symbols])
        self._jumping = True
        try:
            self.engine.reanchor(prices)
            self.history.clear()
            self.save()
            self.timeline += 1
        finally:
            self._jumping = False
            self.start_history()
            self.publish_all()
        return self.real_prices_status()

    async def refresh_real_prices(self, older_than: timedelta = timedelta(0)) -> None:
        """Fetch every symbol's real price (one request, in a worker thread) unless
        the last fetch was more recent than `older_than`. A fetch already running
        is waited for instead of repeated."""
        if not (self.real and self.real.configured):
            return
        async with self._real_lock:
            if not self.real.stale(older_than):
                return
            self.real.attempt()
            try:
                prices = await asyncio.to_thread(self.real.fetch, self.symbols)
            except Exception as e:
                self.real.failed(str(e))
                log.warning("fetching real prices failed: %s", e)
            else:
                self.real.record(prices, self.symbols)

    async def refresh_real_prices_if_stale(self) -> None:
        await self.refresh_real_prices(STATUS_REFRESH_AFTER)

    async def _refresh_real_prices(self) -> None:
        """Keep the saved real prices recent, in the background."""
        while True:
            await self.refresh_real_prices(REFRESH_EVERY)
            await asyncio.sleep(REFRESH_EVERY.total_seconds())

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
        if self.real and self.real.configured:
            self._real_task = asyncio.create_task(self._refresh_real_prices())

    async def stop(self) -> None:
        if self._real_task:
            self._real_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._real_task
        if self._history_task:
            self._history_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._history_task
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self.save()
