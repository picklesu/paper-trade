from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from paper_trade.market.service import MarketService
from paper_trade.trading.broker import OrderError

router = APIRouter()

# All `async def`: they touch market and account state, which lives on the event loop.


def market(request: Request) -> MarketService:
    svc: MarketService = request.app.state.market
    if svc.jumping:
        raise HTTPException(409, "a time jump is in progress")
    return svc


@router.get("/account")
async def get_account(request: Request) -> dict:
    svc = market(request)
    svc.sync()
    return svc.broker.summary(svc.engine.quotes)


class AccountReset(BaseModel):
    starting_cash: float | None = None


@router.post("/account/reset")
async def reset_account(body: AccountReset, request: Request) -> dict:
    svc = market(request)
    try:
        svc.broker.reset(body.starting_cash)
    except OrderError as e:
        raise HTTPException(422, str(e)) from None
    svc.after_trade()
    return svc.broker.summary(svc.engine.quotes)


@router.get("/orders")
async def get_orders(request: Request) -> list[dict]:
    svc = market(request)
    svc.sync()
    return svc.orders_message()["orders"]


class OrderRequest(BaseModel):
    symbol: str
    side: Literal["buy", "sell"]
    qty: int
    type: Literal["market", "limit", "stop", "stop_limit"]
    tif: Literal["day", "gtc"] = "day"
    limit_price: float | None = None
    stop_price: float | None = None


@router.post("/orders")
async def place_order(body: OrderRequest, request: Request) -> dict:
    svc = market(request)
    svc.sync()  # orders go in at the current market time, after earlier orders are matched
    try:
        order = svc.broker.place(**body.model_dump())
    except OrderError as e:
        raise HTTPException(422, str(e)) from None
    svc.after_trade()
    return order.to_dict()


@router.delete("/orders/{order_id}")
async def cancel_order(order_id: int, request: Request) -> dict:
    svc = market(request)
    svc.sync()  # it may have filled since the last tick
    try:
        order = svc.broker.cancel(order_id)
    except OrderError as e:
        raise HTTPException(422, str(e)) from None
    svc.after_trade()
    return order.to_dict()
