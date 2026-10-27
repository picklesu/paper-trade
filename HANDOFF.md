# Handoff

_Last updated: 2026-10-27, after Phase 2a was merged._

## Where things stand

| Phase | Status | PR |
|---|---|---|
| 0. Skeleton | Merged (committed directly to `main`) | — |
| 1. Market engine + live watchlist | Merged, tested by the user on their Mac | #1 |
| `server.sh` on/off script | Merged | #2 |
| 2a. Market sessions + date/time control | Merged. **The user hasn't tested it yet**: ask how it went. | #7 |
| **2b. Persistence, account and orders** | **Next. Not started. Decisions made (below).** | — |

`main` is clean and up to date with GitHub. No open branches or PRs.

## Pick up here

1. Ask the user whether Phase 2a works for them (`./server.sh restart`, then try Go to…).
   Fix anything they report in a follow-up PR first.
2. Build **Phase 2b** on branch `phase-2b-orders`, following the workflow in `CLAUDE.md`.

### Going back in time (decided by the user, 2026-10-27)

Picking a date/time earlier than the current sim time:

1. **Confirmation dialog first.** It lists the trades made after that date that will be erased,
   says their money will be refunded, and shows how held positions will be repriced. The user
   must confirm.
2. **The account is restored to its state at that date**, as if later trades never happened.
   Erased buys refund their cash. Erased sells give the shares back and remove the sale proceeds.
   Realized P&L after the date reverses. Orders placed after the date are erased. Orders that
   were working at the date (including ones that filled later) go back to working.
3. **Positions held at that date get normal P&L adjustments:** they are valued at the market
   prices recorded at that date. Day stats (open, high, low, volume) are rebuilt from that
   day's history up to that time.
4. **The user chooses what happens next, each time (in the same dialog):**
   - **Replay the same prices:** the market repeats exactly what happened before (practice a
     scenario again).
   - **New future:** prices from that date on are freshly generated (no hindsight).
   Either way, the old history after that date is discarded and rebuilt as time moves forward.
5. **Earlier than the market's history** (before it started or was last reset): warn that **all**
   trades will be erased and cash reset to the starting amount, then start a brand-new market at
   that date. Any date works.

**Built in 2a (market side):** steps 3–5 for prices, and the dialog with both choices. The
dialog already says trades will be erased and refunded, but there are no trades yet.
**Still to build in 2b (account side):** steps 1–2. The rewind dialog must list the actual
trades to be erased and show the repricing. Also reset the account in step 5.

- Account rewind is simplest if the account is **rebuilt from a time-stamped list of all fills
  and cash events** (an event log): drop events after the date and recompute. Design the 2b
  schema that way from the start.

### Phase 2b: persistence, account and orders

Decided by the user (2026-10-27):

- **Save across restarts.** The account, orders, fills and positions persist (SQLite file in
  `backend/`, git-ignored). Market state must persist too, or positions would be valued against
  prices that reset on every restart. On restart, resume from the saved sim time; don't count
  the time the server was off.
  - Saving the market is small, because prices are regenerated from seeds. `MarketEngine` needs
    only: `origin`, `_time`, `_epochs` (list of (start time, seed)), `_starts` (per session:
    previous close per symbol plus regime index; it could even be trimmed to the latest
    session plus the epochs, since earlier ones can be regenerated), `_seed` and `_seeds_used`.
    The clock needs speed and paused. No price history or RNG state has to be stored.
- **Cash account only.** No margin and no short selling. Buying power = cash minus cash
  reserved by working buy orders. Reject a sell for more shares than are held.

Scope (agreed except the bullets marked **Proposal**):

- Account: starting cash (e.g. $100,000), cash, buying power, net liquidation value, day P&L
  shown in the top bar (the placeholders already exist in `TopBar.tsx`).
- Orders: market, limit, stop, stop-limit; buy/sell; day vs GTC; cancel working orders.
- **Day orders expire at the 4:00 PM ET session close.**
- Fills: market buys at the ask and sells at the bid; limits fill when the market crosses the
  limit; stops trigger on last price. Working orders must be checked against the price path,
  not just the half-second tick snapshots. A fast-forward jumps whole sessions at a time
  (`advance_iter`), and at 600× each tick covers 5 minutes. Scan the generated per-second path
  (`_DayPath.prices`, `high`, `low`) between the previous and new time for each working order,
  and fill at the first second it triggers.
- Positions: quantity, average cost, market value, unrealized and realized P&L.
- UI: the order ticket in the right panel, and the Positions / Orders / Activity bottom tabs
  (placeholders exist in `BottomTabs.tsx`). Fills are pushed over the existing WebSocket as
  new message types (`StreamMessage` in `frontend/src/lib/types.ts` is a union for this).
- **Proposal:** a "Reset account" action (cash back to the starting amount, positions and
  orders cleared, optional start date), since saved state otherwise never resets.
- Going back in time erases later trades (see above). Store fills and cash movements as a
  time-stamped event log so the account can be rebuilt as of any date.
- Phase 3 (order preview: estimated cost, buying-power impact, order history) builds on this.

## Architecture

- **backend/** (Python 3.14, FastAPI, numpy, uv)
  - `market/calendar.py`: sessions are 9:30 AM–4:00 PM ET, Monday–Friday (no holidays).
    `snap`, `add_trading_seconds`, `trading_days_between` and so on use numpy business-day
    arithmetic, so they take constant time however far apart the dates are.
  - `market/clock.py`: `SimClock`. Market time = anchor + real elapsed × speed, counted in
    open-market seconds, so the 4:00 PM close continues at the next 9:30 AM. Supports
    pause/resume, `set_speed` and `set_time`. The real-time source is injectable for tests.
  - `market/engine.py`: `MarketEngine`. Each session's per-second path (23,400 seconds × 10
    symbols) is generated in one go from numpy draws seeded by `(seed, date)`, so any moment is
    reproducible however the clock stepped there. The model: correlated geometric Brownian
    motion (shared market shock plus each symbol's own noise), a per-minute regime chain
    (calm/normal/volatile), 20% of daily variance as the overnight gap, U-shaped volume, and
    volatility calibrated to 252 trading days. Paths are cached for 4 sessions. A
    "new future" is a new epoch `(start, seed)`: draws after `start` come from the new seed.
    The previous close is the last price shown (row S-1). The regime shown is the one that
    drove the latest move.
  - `market/symbols.py`: 10 symbols (SPY, QQQ, IWM, AAPL, MSFT, NVDA, AMZN, META, TSLA, AMD).
    Prices are synthetic.
  - `market/service.py`: `MarketService`. A background loop advances the engine to `clock.now()`
    every 0.5s and sends `tick` messages. It skips ticks while paused or during a jump, and logs
    and survives a failed tick. `goto()` handles forward (session by session, with `progress`
    messages), rewind and reset. The final snapshot is always sent, with `force=True`, so it
    is never dropped from a full client queue.
  - `api/`: `GET /api/health`, `GET /api/quotes`, `GET|POST /api/clock` (409 during a jump),
    `GET /api/clock/goto?time=` (preview: forward / rewind / reset / none, plus the snapped
    target), `POST /api/clock/goto {time, future: replay|new}`, WebSocket `/ws`. Times without a
    UTC offset are New York wall-clock time; years 1900–2999; forward jumps are capped at 5
    years. **Endpoints that touch market state must be `async def`** so they run on the event
    loop's thread; a plain `def` runs in a worker thread and races the tick loop.
- **frontend/** (React 19, TypeScript, Vite, Vitest)
  - `lib/stream.ts`: one WebSocket with auto-reconnect. `useStream()` returns the latest tick
    and any fast-forward progress (cleared by the next tick).
  - `components/`: `TopBar`, `ClockControls` (Go to…, pause, speeds from `clock.speeds`,
    regime badge or progress), `GoToDialog` (picker with ±day/week shortcuts → preview → forward
    immediately / rewind confirmation with replay or new future / new-market warning),
    `Watchlist` (tick flashes), `QuoteDetail`, `BottomTabs`, `Panel`.
  - Vite proxies `/api` and `/ws` to `localhost:8000`.
- Tests: backend 63 (pytest), frontend 24 (Vitest + Testing Library).

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
- **Real session hours without waiting:** 9:30–4:00 ET, Monday–Friday, with the close jumping to
  the next open (the user chose this over a 24-hour market). The user can go to any date/time.
- **Going back in time:** later trades are erased and refunded, positions are repriced, and the
  user picks replay or new future each time. Before the history, a fresh market (see above).
- Saved across restarts; cash account only (Phase 2b).

## Known limitations / backlog

- Restarting the backend starts a new market at the current real-world time. Phase 2b fixes
  this by saving the market state.
- No exchange holidays; weekends only.
- Phase 4 charts need history bars. They can be built from `_path(day)` (per-second prices and
  per-minute volume) for any past session, since paths are reproducible. Consider caching
  per-minute bars in SQLite once 2b adds it.
- Reset (going before the history) starts at the spec prices. A new market always begins at
  the same prices whatever the date.
- `dev.sh` (foreground) still exists alongside `server.sh` (background). Keep it or remove it.
