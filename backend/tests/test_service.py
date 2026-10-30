import asyncio
from datetime import UTC, datetime

import pytest

from paper_trade.market.clock import SimClock
from paper_trade.market.engine import MarketEngine
from paper_trade.market.service import MarketService
from paper_trade.store import Store

START = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)


class FakeTime:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def make_service() -> tuple[MarketService, FakeTime]:
    real = FakeTime()
    clock = SimClock(start=START, real_time=real)
    return MarketService(clock, MarketEngine(START), Store(":memory:"), history_days=5), real


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


def test_goto_forward_rewind_and_reset() -> None:
    svc, _ = make_service()
    q = svc.subscribe()

    async def run() -> None:
        action, target = await svc.goto(datetime(2026, 3, 2, 15, 0, tzinfo=UTC))
        assert action == "forward"
        assert svc.engine.time == target == svc.clock.now()
        progress = [m for m in drain(q) if m["type"] == "progress"]
        assert progress and progress[-1]["total"] == 38  # Jan 5 to Mar 2, less MLK Day and Presidents' Day

        action, _ = await svc.goto(datetime(2026, 1, 6, 15, 0, tzinfo=UTC), new_future=True)
        assert action == "rewind"
        assert svc.engine.time == svc.clock.now()

        action, target = await svc.goto(datetime(2025, 12, 1, 15, 0, tzinfo=UTC))
        assert action == "reset"
        assert svc.engine.origin == target

    asyncio.run(run())


def test_goto_snaps_to_market_hours() -> None:
    svc, _ = make_service()
    saturday = datetime(2026, 1, 10, 17, 0, tzinfo=UTC)
    action, target = svc.preview_goto(saturday)
    assert action == "forward"
    assert target == datetime(2026, 1, 12, 14, 30, tzinfo=UTC)  # Monday 9:30 ET


def test_goto_rejects_huge_jumps() -> None:
    svc, _ = make_service()
    with pytest.raises(ValueError):
        asyncio.run(svc.goto(datetime(2040, 1, 2, 15, 0, tzinfo=UTC)))


def test_no_ticks_while_jumping() -> None:
    svc, real = make_service()
    svc._jumping = True
    q = svc.subscribe()
    real.t = 5
    svc.tick()
    assert q.empty()


def drain(q: asyncio.Queue) -> list[dict]:
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


def test_final_snapshot_survives_a_full_queue() -> None:
    svc, _ = make_service()
    svc.clock.pause()
    q = svc.subscribe()
    asyncio.run(svc.goto(datetime(2026, 6, 1, 15, 0, tzinfo=UTC)))  # many progress messages
    assert q.full()
    last = drain(q)[-1]
    assert last["type"] == "tick"
    assert last["clock"]["time"].startswith("2026-06-01")


def test_progress_counts_only_sessions_left_to_generate() -> None:
    svc, _ = make_service()
    q = svc.subscribe()

    async def run() -> None:
        await svc.goto(datetime(2026, 3, 2, 15, 0, tzinfo=UTC))
        await svc.goto(datetime(2026, 2, 2, 15, 0, tzinfo=UTC))  # replay rewind
        drain(q)
        await svc.goto(datetime(2026, 4, 1, 15, 0, tzinfo=UTC))

    asyncio.run(run())
    progress = [m for m in drain(q) if m["type"] == "progress"]
    assert progress[-1]["total"] == 22  # Mar 2 -> Apr 1, not Feb 2 -> Apr 1


def test_fill_found_by_any_sync_is_saved_and_announced() -> None:
    svc, real = make_service()
    last = svc.engine.quotes["TSLA"].last
    svc.broker.place("TSLA", "buy", 1, "limit", limit_price=round(last * 0.97, 2), tif="gtc")
    q = svc.subscribe()
    svc.clock.set_speed(600)
    real.t = 3 * 3600  # far enough for a 3% move in TSLA
    svc.sync()  # as GET /api/account or a goto preview would
    orders = [m for m in drain(q) if m["type"] == "orders"]
    assert orders and orders[-1]["orders"][0]["status"] == "filled"
    assert svc.store.load_orders()[0].status == "filled"


def test_fast_forward_fills_match_stepping() -> None:
    a, _ = make_service()
    b, _ = make_service()
    target = datetime(2026, 2, 20, 15, 0, tzinfo=UTC)
    for svc in (a, b):
        last = svc.engine.quotes["NVDA"].last
        svc.broker.place("NVDA", "buy", 1, "limit", limit_price=round(last * 0.9, 2), tif="gtc")
        svc.broker.place("NVDA", "buy", 1, "limit", limit_price=round(last * 0.5, 2), tif="day")
    asyncio.run(a.goto(target))
    t0 = b.engine.time
    b.engine.advance(target)
    b.broker.process(t0, target)
    assert [o.to_dict() for o in a.broker.orders.values()] == [o.to_dict() for o in b.broker.orders.values()]


def test_saves_session_starts_only_when_they_change(monkeypatch) -> None:
    svc, real = make_service()
    keys: list[str] = []
    original = svc.store.set
    monkeypatch.setattr(svc.store, "set", lambda k, v: (keys.append(k), original(k, v)))
    svc.save()
    svc.save()
    assert keys.count("market_starts") == 1
    real.t = 7 * 3600  # into the next session
    svc.sync()
    svc.save()
    assert keys.count("market_starts") == 2


def test_history_build_finishes_with_a_snapshot_that_says_so() -> None:
    svc, _ = make_service()
    svc.clock.pause()
    q = svc.subscribe()

    async def run() -> None:
        svc.start_history()
        assert svc.building_history
        await svc._history_task

    asyncio.run(run())
    ticks = [m for m in drain(q) if m["type"] == "tick"]
    assert ticks and ticks[-1]["clock"]["building_history"] is False
    assert svc.engine.backfilled and svc.engine.origin < datetime(2026, 1, 5, tzinfo=UTC)


def test_market_from_an_older_model_continues_and_rebuilds_history(tmp_path) -> None:
    from paper_trade.main import load_market

    # A market saved by the older model (no model version, no variance), with chart bars.
    store = Store(tmp_path / "old.db")
    svc = MarketService(SimClock(start=START), MarketEngine(START), store, history_days=5)
    asyncio.run(svc.build_history())
    svc.engine.advance(datetime(2026, 1, 8, 15, 0, tzinfo=UTC))
    svc.save()
    state = store.get("market")
    state.pop("model")
    store.set("market", state)
    starts = store.get("market_starts")
    store.set("market_starts", {d: s[:2] for d, s in starts.items()})
    old_days = svc.history.stored_days()

    clock, engine = load_market(store, seed=42)
    continued = engine.time
    assert engine.origin == continued == svc.engine.time
    assert not engine.backfilled
    new = MarketService(clock, engine, store, history_days=5)
    asyncio.run(new.build_history())
    # Five days of new history before that point replace all the old bars.
    days = new.history.stored_days()
    assert len(days) == 5 and max(days) < continued.date() and days != old_days
    assert engine.backfilled and engine.time == continued


def test_a_full_queue_keeps_orders_and_watchlists_messages() -> None:
    svc, _ = make_service()
    q = svc.subscribe()
    for kind in ["orders", "tick", "watchlists"] + ["tick"] * (q.maxsize - 3):
        svc.send(q, {"type": kind}, force=False)
    assert q.full()
    svc.send(q, {"type": "orders", "n": 2})
    svc.send(q, {"type": "watchlists", "n": 2})
    kinds = [m["type"] for m in drain(q)]
    assert kinds.count("orders") == 2 and kinds.count("watchlists") == 2
    assert kinds.count("tick") == q.maxsize - 4
