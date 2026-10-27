# Handoff

_Last updated: 2026-10-27, after Phase 1 and the server script were merged._

## Where things stand

| Phase | Status | PR |
|---|---|---|
| 0. Skeleton | Merged (committed directly to `main`) | — |
| 1. Market engine + live watchlist | Merged, tested by the user on their Mac | #1 |
| `server.sh` on/off script | Merged | #2 |
| **2. Account and orders** | **Next. Not started. One open question (below).** | — |

`main` is clean and up to date with GitHub. No open branches or PRs.

## Pick up here

1. **Ask the user the open question before building Phase 2:** should the account and its
   trades be **saved across restarts** (SQLite in `backend/`) or **reset on every restart**?
   (This was asked at the end of the last session, and the user hadn't answered yet.)
   Persisting the account probably also means persisting the market state (prices and clock),
   or positions will be valued against prices that reset to their starting values. Raise that
   with the user.
2. Then build Phase 2 on branch `phase-2-orders`, following the workflow in `CLAUDE.md`.

### Phase 2 proposed scope (not yet agreed with the user)

- Account: starting cash (e.g. $100,000), cash, buying power, net liquidation value, day P&L
  shown in the top bar (the placeholders already exist in `TopBar.tsx`).
- Orders: market, limit, stop, stop-limit; buy/sell; day vs GTC; cancel working orders.
- Fills: market buys at the ask and sells at the bid; limits fill when the market crosses the
  limit; stops trigger on last price. Check working orders every engine tick in `MarketService`.
- Positions: quantity, average cost, market value, unrealized and realized P&L.
- UI: the order ticket in the right panel, and the Positions / Orders / Activity bottom tabs
  (placeholders exist in `BottomTabs.tsx`). Fills are pushed over the existing WebSocket as
  new message types (`StreamMessage` in `frontend/src/lib/types.ts` is a union for this).
- Decide with the user: cash account only, or allow margin and short selling? Simplest
  default: a cash account with no shorting, and margin later.
- Phase 3 (order preview: estimated cost, buying-power impact, order history) builds on this.

## Architecture

- **backend/** (Python 3.14, FastAPI, uv)
  - `market/clock.py`: `SimClock`. Sim time runs at `speed` × real time (1/10/60/600) and can
    pause. The real-time source is injectable for tests.
  - `market/engine.py`: `MarketEngine`. Seeded, correlated geometric Brownian motion (a shared
    market factor plus each symbol's own noise), calm/normal/volatile regimes, bid/ask, daily
    OHLC and volume. A new trading day starts at midnight New York time.
  - `market/symbols.py`: 10 symbols (SPY, QQQ, IWM, AAPL, MSFT, NVDA, AMZN, META, TSLA, AMD).
    Prices are synthetic.
  - `market/service.py`: `MarketService`. A background loop steps the engine every 0.5s and
    sends `tick` messages to WebSocket subscribers. It skips ticks while paused and logs and
    survives a failed tick.
  - `api/`: `GET /api/health`, `GET /api/quotes`, `GET|POST /api/clock`, WebSocket `/ws`.
    **Endpoints that touch market state must be `async def`** so they run on the event loop's
    thread; a plain `def` runs in a worker thread and races the tick loop (this was a review
    finding in Phase 1).
- **frontend/** (React 19, TypeScript, Vite, Vitest)
  - `lib/stream.ts`: one WebSocket with auto-reconnect; `useStream()` returns the latest tick.
  - `components/`: `TopBar`, `ClockControls` (speed buttons come from the backend's
    `clock.speeds`), `Watchlist` (tick flashes), `QuoteDetail`, `BottomTabs`, `Panel`.
  - Vite proxies `/api` and `/ws` to `localhost:8000`.
- Tests: backend 22 (pytest), frontend 10 (Vitest + Testing Library).

## Decisions already made (don't reopen without a reason)

- **Synthetic market data first**; historical replay later (Phase 12). Real options data is
  too expensive, so option chains will be priced with Black-Scholes using a volatility smile
  (Phase 7).
- **Python backend plus React frontend, run locally.** The user considered **GitHub Pages** and
  chose to keep the local server setup. Pages would require moving the engine into the browser.
- **WebAssembly (WASM):** not for the UI. Rendering and DOM updates are the bottleneck, not
  computation. Later, heavy math can use Web Workers and then WASM, only where measurements
  show a need.
- Single user, local only.

## Known limitations / backlog

- Restarting the backend resets the market to its starting prices (relevant to the Phase 2
  persistence question).
- `snapshot()` reports `clock.now()`, while the quotes reflect `engine.time`. These can differ
  by up to one tick; harmless for now.
- `dev.sh` (foreground) still exists alongside `server.sh` (background). Keep it or remove it.
