from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from paper_trade import __version__
from paper_trade.api import health, stream


def create_app() -> FastAPI:
    app = FastAPI(title="Paper Trade", version=__version__)

    # The Vite dev server proxies /api and /ws, but allow direct access too.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router, prefix="/api")
    app.include_router(stream.router)
    return app


app = create_app()


def run() -> None:
    import uvicorn

    uvicorn.run("paper_trade.main:app", host="0.0.0.0", port=8000, reload=True)
