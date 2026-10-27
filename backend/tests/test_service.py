import asyncio
from datetime import UTC, datetime

from paper_trade.market.clock import SimClock
from paper_trade.market.engine import MarketEngine
from paper_trade.market.service import MarketService

START = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)


class FakeTime:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def make_service() -> tuple[MarketService, FakeTime]:
    real = FakeTime()
    clock = SimClock(start=START, real_time=real)
    return MarketService(clock, MarketEngine(START)), real


def test_tick_publishes_only_when_time_moves() -> None:
    svc, real = make_service()
    q = svc.subscribe()
    real.t = 1
    svc.tick()
    assert q.qsize() == 1

    svc.clock.pause()
    real.t = 2
    svc.tick()
    svc.tick()
    assert q.qsize() == 1


def test_failed_tick_does_not_stop_the_loop(monkeypatch) -> None:
    svc, real = make_service()
    calls = 0

    def flaky_tick() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("boom")

    monkeypatch.setattr(svc, "tick", flaky_tick)
    monkeypatch.setattr("paper_trade.market.service.TICK_SECONDS", 0.01)

    async def run() -> None:
        svc.start()
        await asyncio.sleep(0.1)
        await svc.stop()

    asyncio.run(run())
    assert calls > 1
