"""Runs the market in the background and fans ticks out to subscribers."""

import asyncio
import contextlib
import logging
from datetime import datetime
from typing import Literal

from paper_trade.market.calendar import market_day, snap, trading_days_between
from paper_trade.market.clock import SPEEDS, SimClock
from paper_trade.market.engine import MarketEngine

TICK_SECONDS = 0.5
MAX_JUMP_DAYS = 252 * 5
PROGRESS_EVERY_DAYS = 5

log = logging.getLogger(__name__)

GotoAction = Literal["forward", "rewind", "reset", "none"]


class BusyError(Exception):
    """Another time jump is already running."""


class MarketService:
    def __init__(self, clock: SimClock, engine: MarketEngine) -> None:
        self.clock = clock
        self.engine = engine
        self._subscribers: set[asyncio.Queue[dict]] = set()
        self._task: asyncio.Task | None = None
        self._jumping = False

    def snapshot(self) -> dict:
        return {
            "type": "tick",
            "clock": self.clock_state(),
            "regime": self.engine.regime,
            "quotes": [q.to_dict() for q in self.engine.quotes.values()],
        }

    def clock_state(self) -> dict:
        return {
            "time": self.engine.time.isoformat(),
            "speed": self.clock.speed,
            "paused": self.clock.paused,
            "speeds": SPEEDS,
            "origin": self.engine.origin.isoformat(),
        }

    def tick(self) -> None:
        if self._jumping:
            return
        before = self.engine.time
        self.engine.advance(self.clock.now())
        # Nothing changes while paused; clock updates publish on their own.
        if self.engine.time != before:
            self.publish(self.snapshot())

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

    async def goto(self, target: datetime, new_future: bool = False) -> tuple[GotoAction, datetime]:
        if self._jumping:
            raise BusyError
        action, target = self.preview_goto(target)
        days = trading_days_between(market_day(self.engine.time), market_day(target))
        if action == "forward" and days > MAX_JUMP_DAYS:
            raise ValueError(f"can jump at most {MAX_JUMP_DAYS} trading days ahead")

        self._jumping = True
        try:
            if action == "reset":
                self.engine.reset(target)
            elif action == "rewind":
                self.engine.advance(self.clock.now())
                self.engine.rewind(target, new_future)
            elif action == "forward":
                # Generate session by session, yielding so ticks and progress keep flowing.
                for i, _ in enumerate(self.engine.advance_iter(target), start=1):
                    if i % PROGRESS_EVERY_DAYS == 0:
                        self.publish({"type": "progress", "done": i, "total": days})
                    await asyncio.sleep(0)
            self.clock.set_time(target)
        finally:
            self._jumping = False
        self.publish(self.snapshot())
        return action, target

    def publish(self, msg: dict) -> None:
        for q in self._subscribers:
            # A slow client just misses ticks; the next one carries full state.
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(msg)

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
