# Handoff

_Last updated: 2026-10-27, after Phase 2b was merged._

## Where things stand

| Phase | Status | PR |
|---|---|---|
| 0. Skeleton | Merged (committed directly to `main`) | — |
| 1. Market engine + live watchlist | Merged, tested by the user on their Mac | #1 |
| `server.sh` on/off script | Merged | #2 |
| 2a. Market sessions + date/time control | Merged. Not confirmed by the user yet. | #7 |
| 2b. Persistence, account and orders | Merged. **Not tested by the user yet**: ask how it went. | #8 |
| **3. Order preview and order history** | **Next. Not started.** | — |

`main` is clean and up to date with GitHub. No open branches or PRs.

## Pick up here

1. Ask the user whether Phases 2a and 2b work for them (`./server.sh restart`). Fix anything they
   report in a follow-up PR first.
2. Then Phase 3 (see README roadmap). Phase 2b already has an order ticket with an estimated
   cost and the Orders/Activity tabs, so Phase 3 should focus on the following. Agree the scope
   with the user first.
   - A **preview/confirm step** before sending: estimated cost or proceeds, buying power before
     and after, and position after. Optionally commissions (currently $0).
   - **Ticket feedback:** the result message ("Order #2 is working") goes stale once the order
     fills. Follow the order's status from the stream instead.
   - **Order history:** filters (working / filled / canceled), and details such as the time
     triggered and the time closed with a reason.
   - Possibly **replace/modify** a working order.

## Going back in time (decided by the user; built in 2a + 2b)

Picking an earlier date/time shows a confirmation that lists the trades to be erased and refunded,
the cash before and after, and held positions valued at that time's prices. The user then picks
**Replay the same prices** or **Generate a new future**. Going before the market's history
starts a new market and resets the account. See `Broker.rewind` / `preview_rewind` and
`MarketEngine.rewind`.

## Phase 2b decisions (from the user)

- **Saved across restarts** in SQLite (`backend/paper_trade.db`, git-ignored; `PAPER_TRADE_DB`
  overrides the path). On restart the market resumes at its saved time.
- **Cash account only:** no margin and no short selling. Buying power = cash minus the cash set
  aside for working buy orders (limit/stop-limit at the limit, stop at the stop, market at the ask).
- Day orders expire at the 4:00 PM close. **Reset account…** is in the bottom tab bar (choose
  the starting cash).

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
  - `market/service.py`: `MarketService` owns the clock, engine, store and `Broker`. A
    background loop runs `sync()` every 0.5s (advance the engine to `clock.now()`, then
    `broker.process(t0, t1)`; if orders changed, `sync()` itself saves and sends an `orders`
    message, so every caller gets that) and sends `tick` messages, which include the account. It saves the market about every 5s and after any trade,
    jump or clock change. It skips ticks while paused or during a jump. `goto()` handles
    forward (sessions, then order matching, both yielding), rewind (engine + broker) and reset
    (engine + broker). The final state after a jump is always sent with `force=True`.
    `goto_details()` builds the rewind/reset confirmation contents.
  - `trading/broker.py`: `Broker`, the cash account. `place`/`cancel` validate buying power and
    shares and fill at once when marketable. `process_iter(t0, t1)` scans each session's
    per-second path (`engine.day_prices`) for every working order and handles events in time
    order. It expires day orders at each close. The ledger (cash, positions, average cost,
    realized P&L) is rebuilt from filled orders and cached. `summary()` gives net liq, buying
    power, day P&L (vs holdings at the previous close) and positions. `rewind(t)` deletes
    orders created after `t` and rolls back later fills, closes and triggers. `reset()`. Fill
    rules are in the module docstring.
  - `trading/orders.py`: the `Order` dataclass. Every status change is time-stamped
    (`created_at`, `triggered_at`, `filled_at`, `closed_at`).
  - `store.py`: SQLite. A `meta` table holds JSON for `market` (`engine.to_state()` without
    `starts`, saved about every 5s), `market_starts` (written only when
    `engine.starts_version` changes), `clock` and `starting_cash`; an `orders` table holds
    one JSON row per order. Startup
    (`main.load_market`) resumes from it, and `MarketService` rolls back any order newer than
    the saved market time.
  - `api/`: `GET /api/health`, `GET /api/quotes`, `GET|POST /api/clock` (409 during a jump),
    `GET /api/clock/goto?time=` (preview: forward / rewind / reset / none, plus the snapped
    target), `POST /api/clock/goto {time, future: replay|new}`, WebSocket `/ws`. Times without a
    UTC offset are New York wall-clock time; years 1900–2999; forward jumps are capped at 5
    years. `api/deps.py`: `market` (read-only) and `idle_market` (409 during a jump) for
    anything that syncs or changes state. Trading (`api/trading.py`): `GET /api/account`, `POST /api/account/reset`,
    `GET|POST /api/orders`, `DELETE /api/orders/{id}`; 422 responses carry a user-readable
    `detail`; 409 during a jump. **Endpoints that touch market state must be `async def`** so
    they run on the event loop's thread; a plain `def` runs in a worker thread and races the
    tick loop.
- **frontend/** (React 19, TypeScript, Vite, Vitest)
  - `lib/stream.ts`: one WebSocket with auto-reconnect. `useStream()` returns the latest tick
    (quotes, clock, account), the orders list, and any fast-forward progress (cleared by the
    next tick).
  - `components/`: `TopBar`, `ClockControls` (Go to…, pause, speeds from `clock.speeds`,
    regime badge or progress), `GoToDialog` (picker with ±day/week shortcuts → preview → forward
    immediately / rewind confirmation with replay or new future / new-market warning),
    `Watchlist` (tick flashes), `QuoteDetail`, `OrderTicket` (side, quantity, type,
    limit/stop, time in force, estimated cost, result), `BottomTabs` (Positions / Orders with
    Cancel / Activity, plus "Reset account…"), `ResetAccountDialog`, `Panel`. The top bar
    shows net liq, buying power and day P&L.
  - Vite proxies `/api` and `/ws` to `localhost:8000`.
- Tests: backend 114 (pytest), frontend 30 (Vitest + Testing Library).

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
- Saved across restarts; cash account only (Phase 2b). No commissions.

## Known limitations / backlog

- Fills are all-or-nothing (no partial fills) and ignore volume and liquidity.
- A stop buy reserves the higher of its stop price and the ask at placement. If the market gaps
  past that, the fill can cost more than reserved. A fill may use only cash not set aside for
  other working buys; otherwise the order is rejected.
- The ledger is rebuilt from all fills when it changes. That's fine for thousands of trades;
  revisit if history grows very large.
- No exchange holidays; weekends only.
- Phase 4 charts need history bars. They can be built from `_path(day)` (per-second prices and
  per-minute volume) for any past session, since paths are reproducible. Consider caching
  per-minute bars in SQLite once 2b adds it.
- Reset (going before the history) starts at the spec prices. A new market always begins at
  the same prices whatever the date.
- `dev.sh` (foreground) still exists alongside `server.sh` (background). Keep it or remove it.
