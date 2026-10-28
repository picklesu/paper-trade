"""Runs the market and the account in the background and fans updates out to
subscribers. Everything here runs on the event loop's thread."""

import asyncio
import contextlib
import logging
from datetime import datetime
from typing import Literal

from paper_trade.market.calendar import market_day, session_open, snap, trading_days_between
from paper_trade.market.clock import SPEEDS, SimClock
from paper_trade.market.engine import MarketEngine
from paper_trade.store import Store
from paper_trade.trading.broker import Broker

TICK_SECONDS = 0.5
SAVE_EVERY_TICKS = 10  # save the market state about every 5 seconds while it runs
MAX_JUMP_DAYS = 252 * 5
PROGRESS_EVERY_DAYS = 5

log = logging.getLogger(__name__)

GotoAction = Literal["forward", "rewind", "reset", "none"]


class BusyError(Exception):
    """Another time jump is already running."""


class MarketService:
    def __init__(self, clock: SimClock, engine: MarketEngine, store: Store) -> None:
        self.clock = clock
        self.engine = engine
        self.store = store
        self.broker = Broker(engine, store)
        # Anything saved after the market's last saved time never happened.
        self.broker.rewind(engine.time)
        self._subscribers: set[asyncio.Queue[dict]] = set()
        self._task: asyncio.Task | None = None
        self._jumping = False
        self._ticks_since_save = 0
        self._saved_starts_version: int | None = None

    @property
    def jumping(self) -> bool:
        return self._jumping

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
        return {"type": "orders", "orders": [o.to_dict() for o in orders]}

    def clock_state(self) -> dict:
        return {
            "time": self.engine.time.isoformat(),
            "speed": self.clock.speed,
            "paused": self.clock.paused,
            "speeds": SPEEDS,
            "origin": self.engine.origin.isoformat(),
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
                "erased_fills": [o.to_dict() for o in sorted(fills, key=lambda o: o.filled_at)],
                "erased_orders": len(self.broker.orders),
                "starting_cash": self.broker.starting_cash,
            }
        return {}

    async def goto(self, target: datetime, new_future: bool = False) -> tuple[GotoAction, datetime]:
        if self._jumping:
            raise BusyError
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
                self.engine.reset(target)
                self.broker.reset()
            elif action == "rewind":
                self.engine.rewind(target, new_future)
                self.broker.rewind(target)
            elif action == "forward":
                await self._fast_forward(target)
            self.clock.set_time(target)
            self.save()
        finally:
            self._jumping = False
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
        for i, day in enumerate(self.engine.advance_iter(target), start=1):
            self.broker.process(t, session_open(day))
            t = session_open(day)
            if i % PROGRESS_EVERY_DAYS == 0:
                self.publish({"type": "progress", "done": i, "total": days})
            await asyncio.sleep(0)
        self.broker.process(t, target)

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

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self.save()
