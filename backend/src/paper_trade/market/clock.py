"""Simulated market clock.

Market time advances at `speed` times real time, but only while the market is
open: at the 4:00 PM close it continues straight from the next session's open.
It can be paused or set to any time. The real-time source is injectable for
tests.
"""

import time
from collections.abc import Callable
from datetime import UTC, datetime

from paper_trade.market.calendar import add_trading_seconds, snap

SPEEDS = (1, 10, 60, 600)


class SimClock:
    def __init__(
        self,
        start: datetime | None = None,
        speed: float = 1,
        paused: bool = False,
        real_time: Callable[[], float] = time.monotonic,
    ) -> None:
        self._real_time = real_time
        self._anchor_sim = snap(start or datetime.now(UTC))
        self._anchor_real = real_time()
        self._speed = speed
        self._paused = paused

    @property
    def speed(self) -> float:
        return self._speed

    @property
    def paused(self) -> bool:
        return self._paused

    def now(self) -> datetime:
        if self._paused:
            return self._anchor_sim
        elapsed = self._real_time() - self._anchor_real
        return add_trading_seconds(self._anchor_sim, elapsed * self._speed)

    def _reanchor(self) -> None:
        self._anchor_sim = self.now()
        self._anchor_real = self._real_time()

    def set_speed(self, speed: float) -> None:
        if speed <= 0:
            raise ValueError("speed must be positive")
        self._reanchor()
        self._speed = speed

    def set_time(self, t: datetime) -> None:
        """Jump to `t` (snapped to market hours), keeping speed and pause state."""
        self._anchor_sim = snap(t)
        self._anchor_real = self._real_time()

    def pause(self) -> None:
        self._reanchor()
        self._paused = True

    def resume(self) -> None:
        self._reanchor()
        self._paused = False
