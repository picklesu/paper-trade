"""Runs the market in the background and fans ticks out to subscribers."""

import asyncio
import contextlib
import logging

from paper_trade.market.clock import SPEEDS, SimClock
from paper_trade.market.engine import MarketEngine

TICK_SECONDS = 0.5

log = logging.getLogger(__name__)


class MarketService:
    def __init__(self, clock: SimClock, engine: MarketEngine) -> None:
        self.clock = clock
        self.engine = engine
        self._subscribers: set[asyncio.Queue[dict]] = set()
        self._task: asyncio.Task | None = None

    def snapshot(self) -> dict:
        return {
            "type": "tick",
            "clock": self.clock_state(),
            "regime": self.engine.regime,
            "quotes": [q.to_dict() for q in self.engine.quotes.values()],
        }

    def clock_state(self) -> dict:
        return {
            "time": self.clock.now().isoformat(),
            "speed": self.clock.speed,
            "paused": self.clock.paused,
            "speeds": SPEEDS,
        }

    def tick(self) -> None:
        before = self.engine.time
        self.engine.step(self.clock.now())
        # Nothing changes while paused; clock updates publish on their own.
        if self.engine.time != before:
            self.publish(self.snapshot())

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
