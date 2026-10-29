import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from paper_trade import __version__
from paper_trade.api import health, market, stream, trading
from paper_trade.market.clock import SimClock
from paper_trade.market.engine import HISTORY_DAYS, MODEL_VERSION, MarketEngine
from paper_trade.market.service import MarketService
from paper_trade.store import Store

# backend/paper_trade.db unless PAPER_TRADE_DB says otherwise.
DEFAULT_DB = Path(__file__).resolve().parents[2] / "paper_trade.db"


def load_market(store: Store, seed: int) -> tuple[SimClock, MarketEngine]:
    """Resume the saved market where it left off, or start one at the current time."""
    state = store.get("market")
    if state is None:
        clock = SimClock()
        return clock, MarketEngine(start=clock.now(), seed=seed)
    if "starts" not in state:  # saved separately since it can be large
        state = {**state, "starts": store.get("market_starts", {})}
    if state.get("model") == MODEL_VERSION:
        engine = MarketEngine.from_state(state)
    else:
        engine = MarketEngine.continue_older(state)
    saved = store.get("clock", {})
    clock = SimClock(start=engine.time, speed=saved.get("speed", 1), paused=saved.get("paused", False))
    return clock, engine


def create_app(seed: int = 42, db_path: str | Path | None = None, history_days: int = HISTORY_DAYS) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        store = Store(db_path or os.environ.get("PAPER_TRADE_DB") or DEFAULT_DB)
        clock, engine = load_market(store, seed)
        svc = MarketService(clock, engine, store, history_days)
        app.state.market = svc
        svc.start()
        yield
        await svc.stop()
        store.close()

    app = FastAPI(title="Paper Trade", version=__version__, lifespan=lifespan)

    # The Vite dev server proxies /api and /ws, but allow direct access too.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router, prefix="/api")
    app.include_router(market.router, prefix="/api")
    app.include_router(trading.router, prefix="/api")
    app.include_router(stream.router)
    return app


app = create_app()


def run() -> None:
    import uvicorn

    uvicorn.run("paper_trade.main:app", host="0.0.0.0", port=8000, reload=True)
