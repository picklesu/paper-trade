from fastapi import HTTPException, Request

from paper_trade.market.service import MarketService

# Endpoints using these are `async def`: market and account state live on the event loop.


def market(request: Request) -> MarketService:
    """The market service, for read-only endpoints."""
    return request.app.state.market


def idle_market(request: Request) -> MarketService:
    """The market service for endpoints that change or sync state: refused during a time jump."""
    svc = market(request)
    if svc.jumping:
        raise HTTPException(409, "a time jump is in progress")
    return svc
