"""Watchlists, the symbol list for lookups, and the extra quote data the
watchlist and quote header show (52-week ranges, average volume, sparklines)."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from paper_trade.api.deps import market

router = APIRouter()


@router.get("/symbols")
async def get_symbols(request: Request) -> list[dict]:
    return [{"symbol": s.symbol, "name": s.name, "sector": s.sector} for s in market(request).engine.specs]


@router.get("/quote-stats")
async def get_quote_stats(request: Request) -> dict:
    """{symbol: {high_52w, low_52w, avg_volume}} from finished sessions (not today)."""
    return market(request).quote_stats()


@router.get("/sparklines")
async def get_sparklines(request: Request, symbols: str) -> dict:
    """Today's prices every few minutes for comma-separated `symbols`."""
    return market(request).sparklines([s.strip().upper() for s in symbols.split(",") if s.strip()])


@router.get("/watchlists")
async def get_watchlists(request: Request) -> list[dict]:
    return market(request).watchlists.lists


class NewWatchlist(BaseModel):
    name: str
    symbols: list[str] = []


class WatchlistUpdate(BaseModel):
    name: str | None = None
    symbols: list[str] | None = None


@router.post("/watchlists")
async def create_watchlist(body: NewWatchlist, request: Request) -> dict:
    svc = market(request)
    try:
        wl = svc.watchlists.create(body.name, body.symbols)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    svc.watchlists_changed()
    return wl


@router.patch("/watchlists/{list_id}")
async def update_watchlist(list_id: int, body: WatchlistUpdate, request: Request) -> dict:
    svc = market(request)
    try:
        wl = svc.watchlists.update(list_id, body.name, body.symbols)
    except KeyError:
        raise HTTPException(404, "no such watchlist") from None
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    svc.watchlists_changed()
    return wl


@router.delete("/watchlists/{list_id}")
async def delete_watchlist(list_id: int, request: Request) -> dict:
    svc = market(request)
    try:
        svc.watchlists.delete(list_id)
    except KeyError:
        raise HTTPException(404, "no such watchlist") from None
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    svc.watchlists_changed()
    return {"deleted": list_id}
