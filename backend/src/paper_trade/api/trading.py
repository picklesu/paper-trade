from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from paper_trade.api.deps import idle_market as market
from paper_trade.trading.broker import OrderError

router = APIRouter()


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


@router.post("/orders/preview")
async def preview_order(body: OrderRequest, request: Request) -> dict:
    """Estimated price, totals, buying power and position after, and warnings."""
    svc = market(request)
    svc.sync()
    try:
        return svc.broker.preview(**body.model_dump())
    except OrderError as e:
        raise HTTPException(422, str(e)) from None


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


class OrderChange(BaseModel):
    qty: int | None = None
    limit_price: float | None = None
    stop_price: float | None = None
    tif: Literal["day", "gtc"] | None = None


@router.patch("/orders/{order_id}")
async def replace_order(order_id: int, body: OrderChange, request: Request) -> dict:
    """Change a working order (cancel/replace): returns the new order."""
    svc = market(request)
    svc.sync()  # it may have filled since the last tick
    try:
        order = svc.broker.replace(order_id, **body.model_dump())
    except OrderError as e:
        raise HTTPException(422, str(e)) from None
    svc.after_trade()
    return order.to_dict()
