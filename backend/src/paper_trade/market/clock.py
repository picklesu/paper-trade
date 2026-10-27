"""Simulated market clock.

Sim time advances at `speed` times real time and can be paused, so the market
can run at any hour and be fast-forwarded. The real-time source is injectable
for tests.
"""

import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

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
        self._anchor_sim = start or datetime.now(UTC)
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
        return self._anchor_sim + timedelta(seconds=elapsed * self._speed)

    def _reanchor(self) -> None:
        self._anchor_sim = self.now()
        self._anchor_real = self._real_time()

    def set_speed(self, speed: float) -> None:
        if speed <= 0:
            raise ValueError("speed must be positive")
        self._reanchor()
        self._speed = speed

    def pause(self) -> None:
        self._reanchor()
        self._paused = True

    def resume(self) -> None:
        self._reanchor()
        self._paused = False
