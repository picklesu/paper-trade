"""Watchlists, the symbol list for lookups, the extra quote data the watchlist
and quote header show (52-week ranges, average volume, sparklines), and option
chains."""

from datetime import date

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from paper_trade.api.deps import market

router = APIRouter()


@router.get("/symbols")
async def get_symbols(request: Request) -> list[dict]:
    svc = market(request)
    stocks = [{"symbol": s.symbol, "name": s.name, "sector": s.sector, "tradable": True} for s in svc.engine.specs]
    return stocks + [{"symbol": q["symbol"], "name": q["name"], "sector": None, "tradable": False} for q in svc.index_quotes()]


@router.get("/quote-stats")
async def get_quote_stats(request: Request) -> dict:
    """{symbol: {high_52w, low_52w, avg_volume}} from finished sessions (not today)."""
    return market(request).quote_stats()


@router.get("/sparklines")
async def get_sparklines(request: Request, symbols: str) -> dict:
    """Today's prices every few minutes for comma-separated `symbols`."""
    return market(request).sparklines([s.strip().upper() for s in symbols.split(",") if s.strip()])


@router.get("/options/expirations")
async def get_option_expirations(request: Request, symbol: str) -> list[dict]:
    """{date, kind, days} for each open expiration, soonest first."""
    svc = market(request)
    if symbol.upper() not in svc.symbols:
        raise HTTPException(404, f"unknown symbol: {symbol}")
    return svc.option_expirations(symbol.upper())


@router.get("/options/chain")
async def get_option_chain(request: Request, symbol: str, expiration: str) -> dict:
    """Every strike's call and put for one expiration (columns of values by strike)."""
    try:
        return market(request).option_chain(symbol.upper(), date.fromisoformat(expiration))
    except ValueError as e:
        raise HTTPException(422, str(e)) from None


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
