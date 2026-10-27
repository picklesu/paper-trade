# Handoff

_Last updated: 2026-10-27, after Phase 1 and the server script were merged; Phase 2 decisions recorded._

## Where things stand

| Phase | Status | PR |
|---|---|---|
| 0. Skeleton | Merged (committed directly to `main`) | — |
| 1. Market engine + live watchlist | Merged, tested by the user on their Mac | #1 |
| `server.sh` on/off script | Merged | #2 |
| **2a. Market sessions + date/time control** | **Next. Not started. Decisions made (below).** | — |
| 2b. Persistence, account and orders | After 2a. Decisions made (below). | — |

`main` is clean and up to date with GitHub. No open branches or PRs.

## Pick up here

Phase 2 is split into two PRs so each can be tested on its own. Follow the workflow in
`CLAUDE.md`.

1. **Phase 2a** on branch `phase-2a-sessions`: market sessions and date/time control.
2. **Phase 2b** on branch `phase-2b-orders`: persistence, the account and orders.

### Phase 2a: market sessions and date/time control

Decided by the user (2026-10-27): **real session hours, without waiting for them**, and
**the user can change the date/time**.

- The sim market trades only from **9:30 AM to 4:00 PM ET, Monday–Friday**. At 4:00 PM the clock
  **jumps straight to the next session's 9:30 AM open** (skipping weekends). Because the sim clock
  only ever runs in session, the user can still trade at any real hour.
- Session open and close events: at the close, record each symbol's closing price. At the open,
  apply an **overnight gap** (a random return with roughly 20–25% of a day's variance, sharing
  the market factor), then reset open, high, low and volume. `prev_close` = the previous
  session's close. The trading-day rollover moves from midnight to the session open.
- **Recalibrate volatility to trading time:** a year = 252 sessions × 6.5h, not 365 × 24h. Spread
  each symbol's daily volume over 6.5h.
- **Date/time control** in the top bar: a "Go to" date/time picker next to the speed buttons.
  - **Forward:** fast-forward the market through the skipped time, session by session. Prices,
    overnight gaps and day rollovers happen along the way, and later on (2b) working orders fill
    or trigger and day orders expire in sequence. Step in chunks (e.g. 5 sim-minutes) so later
    candlestick charts (Phase 4) have history for the skipped period. Show progress for long
    jumps.
  - A target outside session hours snaps to the next 9:30 AM open.
  - **Backward (proposal, confirm with the user):** not allowed as a plain jump. Positions and
    fills can't be un-done. Instead, "Reset account" (2b) lets the user pick a start date and
    starts a fresh market and account from there. Before 2b exists, going backward can simply
    restart the market at the chosen date.
- Top bar shows market status: Open (normal session), plus the market date and time.
- Proposal: ignore exchange holidays for now (weekends only). NYSE holidays can come later.
- Tests: session boundaries (Friday 4:00 PM jumps to Monday 9:30 AM), gap applied once per open,
  snapping, forward jump equivalent to stepping, realized volatility per trading day.

### Phase 2b: persistence, account and orders

Decided by the user (2026-10-27):

- **Save across restarts.** The account, orders, fills and positions persist (SQLite file in
  `backend/`, git-ignored). Market state must persist too: each symbol's prices and session
  stats, the regime, the sim clock, and the random generator's state. Otherwise positions
  would be valued against prices that reset on every restart. On restart, resume from the
  saved sim time; don't count the time the server was off.
- **Cash account only.** No margin and no short selling. Buying power = cash minus cash
  reserved by working buy orders. Reject a sell for more shares than are held.

Scope (agreed except the bullets marked **Proposal**):

- Account: starting cash (e.g. $100,000), cash, buying power, net liquidation value, day P&L
  shown in the top bar (the placeholders already exist in `TopBar.tsx`).
- Orders: market, limit, stop, stop-limit; buy/sell; day vs GTC; cancel working orders.
- **Day orders expire at the 4:00 PM ET session close.**
- Fills: market buys at the ask and sells at the bid; limits fill when the market crosses the
  limit; stops trigger on last price. Check working orders every engine step in
  `MarketService` (including steps during a fast-forward jump).
- Positions: quantity, average cost, market value, unrealized and realized P&L.
- UI: the order ticket in the right panel, and the Positions / Orders / Activity bottom tabs
  (placeholders exist in `BottomTabs.tsx`). Fills are pushed over the existing WebSocket as
  new message types (`StreamMessage` in `frontend/src/lib/types.ts` is a union for this).
- **Proposal:** a "Reset account" action (cash back to the starting amount, positions and
  orders cleared, optional start date), since saved state otherwise never resets.
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

- Restarting the backend resets the market to its starting prices. Phase 2b fixes this by
  persisting the market state.
- Phase 1 runs a 24-hour market with the day rolling over at midnight ET, and volatility
  calibrated to 365 × 24h. Phase 2a replaces this with session hours.
- `snapshot()` reports `clock.now()`, while the quotes reflect `engine.time`. These can differ
  by up to one tick; harmless for now.
- `dev.sh` (foreground) still exists alongside `server.sh` (background). Keep it or remove it.
