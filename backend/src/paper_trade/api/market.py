import asyncio
import contextlib
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from paper_trade.market.calendar import MARKET_TZ
from paper_trade.market.clock import SPEEDS
from paper_trade.api.deps import idle_market, market
from paper_trade.market.service import STATUS_FETCH_WAIT_SECONDS, BusyError

router = APIRouter()

# Keeps date arithmetic well inside what Python dates can represent.
MIN_YEAR, MAX_YEAR = 1900, 2999

# Endpoints that touch the market are `async def` so they run on the event loop,
# the same thread as the tick loop. A plain `def` would run in a worker thread
# and race with the engine and the subscriber queues.


class ClockUpdate(BaseModel):
    speed: int | None = None
    paused: bool | None = None


@router.get("/quotes")
async def get_quotes(request: Request) -> list[dict]:
    svc = market(request)
    return svc.listed_quotes()


@router.get("/clock")
async def get_clock(request: Request) -> dict:
    return market(request).clock_state()


@router.post("/clock")
async def update_clock(update: ClockUpdate, request: Request) -> dict:
    svc = idle_market(request)
    if update.speed is not None:
        if update.speed not in SPEEDS:
            raise HTTPException(422, f"speed must be one of {SPEEDS}")
        svc.clock.set_speed(update.speed)
    if update.paused is True:
        svc.clock.pause()
    elif update.paused is False:
        svc.clock.resume()
    # Catch the engine up to the (possibly now paused) clock so what's saved is
    # exactly where the market stands, then push it right away.
    svc.sync()
    svc.save()
    svc.publish(svc.snapshot(), force=True)
    return svc.clock_state()


def parse_market_time(value: str) -> datetime:
    """ISO date/time; without a UTC offset it is New York wall-clock time."""
    try:
        t = datetime.fromisoformat(value)
    except ValueError:
        raise HTTPException(422, f"not an ISO date/time: {value!r}") from None
    if not MIN_YEAR <= t.year <= MAX_YEAR:
        raise HTTPException(422, f"pick a year between {MIN_YEAR} and {MAX_YEAR}")
    return t if t.tzinfo else t.replace(tzinfo=MARKET_TZ)


@router.get("/clock/goto")
async def preview_goto(time: str, request: Request) -> dict:
    """What going to `time` would do, so the UI can confirm first."""
    svc = idle_market(request)
    action, target = svc.preview_goto(parse_market_time(time))
    return {"action": action, "target": target.isoformat(), **svc.goto_details(action, target)}


class Goto(BaseModel):
    time: str
    # Only used when going back: replay the same prices or generate a new future.
    future: Literal["replay", "new"] = "replay"


@router.post("/clock/goto")
async def goto(body: Goto, request: Request) -> dict:
    svc = market(request)
    try:
        action, target = await svc.goto(parse_market_time(body.time), new_future=body.future == "new")
    except BusyError as e:
        raise HTTPException(409, str(e)) from None
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    return {"action": action, "target": target.isoformat(), "clock": svc.clock_state()}


@router.post("/shock")
async def trigger_shock(request: Request) -> dict:
    """Start an uncertainty shock at the next open (for practice)."""
    svc = market(request)
    try:
        day = svc.trigger_shock()
    except BusyError as e:
        raise HTTPException(409, str(e)) from None
    return {"day": day.isoformat(), "clock": svc.clock_state()}


@router.get("/real-prices")
async def real_prices(request: Request) -> dict:
    """How many symbols have a saved real price, and from when (fetching first if
    the last fetch was over a minute ago)."""
    svc = market(request)
    # A slow fetch carries on in the background; the status says what's saved so far.
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(asyncio.shield(svc.refresh_real_prices_if_stale()), STATUS_FETCH_WAIT_SECONDS)
    return svc.real_prices_status()


@router.post("/real-prices/sync")
async def sync_real_prices(request: Request) -> dict:
    """Continue the market from now at the saved real prices."""
    svc = market(request)
    try:
        status = await svc.sync_real_prices()
    except BusyError as e:
        raise HTTPException(409, str(e)) from None
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    return {**status, "clock": svc.clock_state()}


@router.get("/bars")
async def get_bars(request: Request, symbol: str, interval: str = "5m", end: str | None = None, count: int = 500) -> dict:
    """Chart bars: up to `count` before `end` (default now, including the live bar), oldest first."""
    svc = market(request)
    if symbol not in svc.engine.quotes and symbol not in svc.indexes:
        raise HTTPException(422, f"Unknown symbol {symbol}")
    try:
        return svc.bars(symbol, interval, parse_market_time(end) if end else None, count)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
