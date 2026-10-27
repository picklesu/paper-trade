from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from paper_trade.market.clock import SPEEDS
from paper_trade.market.service import MarketService

router = APIRouter()


def market(request: Request) -> MarketService:
    return request.app.state.market


class ClockUpdate(BaseModel):
    speed: int | None = None
    paused: bool | None = None


@router.get("/quotes")
def get_quotes(request: Request) -> list[dict]:
    return [q.to_dict() for q in market(request).engine.quotes.values()]


@router.get("/clock")
def get_clock(request: Request) -> dict:
    return {**market(request).clock_state(), "speeds": SPEEDS}


@router.post("/clock")
def update_clock(update: ClockUpdate, request: Request) -> dict:
    svc = market(request)
    if update.speed is not None:
        if update.speed not in SPEEDS:
            raise HTTPException(422, f"speed must be one of {SPEEDS}")
        svc.clock.set_speed(update.speed)
    if update.paused is True:
        svc.clock.pause()
    elif update.paused is False:
        svc.clock.resume()
    # Push the new clock state right away instead of waiting for the next tick.
    svc.publish(svc.snapshot())
    return get_clock(request)
