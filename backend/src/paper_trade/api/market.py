from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from paper_trade.market.calendar import MARKET_TZ
from paper_trade.market.clock import SPEEDS
from paper_trade.market.service import BusyError, MarketService

router = APIRouter()

# Keeps date arithmetic well inside what Python dates can represent.
MIN_YEAR, MAX_YEAR = 1900, 2999

# Endpoints that touch the market are `async def` so they run on the event loop,
# the same thread as the tick loop. A plain `def` would run in a worker thread
# and race with the engine and the subscriber queues.


def market(request: Request) -> MarketService:
    return request.app.state.market


class ClockUpdate(BaseModel):
    speed: int | None = None
    paused: bool | None = None


@router.get("/quotes")
async def get_quotes(request: Request) -> list[dict]:
    return [q.to_dict() for q in market(request).engine.quotes.values()]


@router.get("/clock")
async def get_clock(request: Request) -> dict:
    return market(request).clock_state()


@router.post("/clock")
async def update_clock(update: ClockUpdate, request: Request) -> dict:
    svc = market(request)
    if svc.jumping:
        raise HTTPException(409, "a time jump is in progress")
    if update.speed is not None:
        if update.speed not in SPEEDS:
            raise HTTPException(422, f"speed must be one of {SPEEDS}")
        svc.clock.set_speed(update.speed)
    if update.paused is True:
        svc.clock.pause()
    elif update.paused is False:
        svc.clock.resume()
    # Push the new clock state right away instead of waiting for the next tick.
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
    action, target = market(request).preview_goto(parse_market_time(time))
    return {"action": action, "target": target.isoformat()}


class Goto(BaseModel):
    time: str
    # Only used when going back: replay the same prices or generate a new future.
    future: Literal["replay", "new"] = "replay"


@router.post("/clock/goto")
async def goto(body: Goto, request: Request) -> dict:
    svc = market(request)
    try:
        action, target = await svc.goto(parse_market_time(body.time), new_future=body.future == "new")
    except BusyError:
        raise HTTPException(409, "a time jump is already in progress") from None
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    return {"action": action, "target": target.isoformat(), "clock": svc.clock_state()}
