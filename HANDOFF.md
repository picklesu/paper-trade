# Handoff

_Last updated: 2026-10-27, after Phase 2b was merged._

## Where things stand

| Phase | Status | PR |
|---|---|---|
| 0–3. Skeleton, market engine, sessions, time travel, account, orders, order review | Merged; the user confirmed 2a–3 work | #1–#9 |
| CI (GitHub Actions: tests, build, lint on every PR) | Merged | #10 |
| 4. Interactive charts + 5 years of history | Merged | #11 |
| 5. Indicators | Merged | #12 |
| **6. Watchlists and quotes** | **Next. Not started.** | — |

`main` is clean and up to date with GitHub. No open branches or PRs.

## Pick up here

**Phase 6: watchlists and quotes** (README roadmap). Agree the scope with the user first. Ideas:
editable watchlists (add or remove symbols from those available, several named lists), more
columns (bid/ask, volume, day range, 52-week range from history), sorting, and a fuller quote
panel. Adding symbols beyond the fixed 10 would mean extending `market/symbols.py` and the
engine's symbol arrays. Changing the symbol set changes every price path (the random draws are
per symbol column), so treat that as a new market or make draws per symbol.

User preferences so far: a **fully interactive** chart; ranges from 1 minute to 5 years; a
standard indicator set. **The user is not QA**: verify everything (tests, build, lint with
`--deny-warnings`, a headless-browser check that **looks at screenshots**, green CI) before
merging, and don't ask them to test each phase.

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
    drove the latest move. `backfill_iter(days)` generates history before the origin, newest
    first: each day's path comes from a start of 1.0, scaled so it closes at the next day's
    previous close. Paths are multiplicative, so replaying forward gives the same prices, and
    going back in time works the same inside this history. `minute_bars(day, through)` gives OHLCV
    per minute.
  - `market/history.py`: `History`, the chart bars. Finished sessions are stored as 30-minute
    bars in a `bars30` table (recorded via `engine.on_day_complete`, in batches during long
    jumps and the history build). `bars(symbol, interval, end, count)` serves 30m/1h/1D/1W from
    the table, plus today's live bars, and serves 1m/5m/15m from the engine's paths (looking back
    60/252/252 sessions). Paging back with `end` never splits a bar or a week. A replay rewind
    hides later stored days (`day < today`); a new future deletes them (`delete_from`); a reset
    clears everything.
  - `market/symbols.py`: 10 symbols (SPY, QQQ, IWM, AAPL, MSFT, NVDA, AMZN, META, TSLA, AMD).
    Prices are synthetic.
  - `market/service.py`: `MarketService` owns the clock, engine, store and `Broker`. A
    background loop runs `sync()` every 0.5s (advance the engine to `clock.now()`, then
    `broker.process(t0, t1)`; if orders changed, `sync()` itself saves and sends an `orders`
    message, so every caller gets that) and sends `tick` messages, which include the account. It saves the market about every 5s and after any trade,
    jump or clock change. It skips ticks while paused or during a jump. `goto()` handles
    forward (sessions, then order matching, both yielding), rewind (engine + broker) and reset
    (engine + broker). The final state after a jump is always sent with `force=True`.
    `goto_details()` builds the rewind/reset confirmation contents. `build_history()` runs in the
    background at startup and after a reset: it records missing finished sessions and backfills
    `HISTORY_DAYS` (5 years) if `engine.backfilled` is false (about 8s), sending `history`
    progress messages. The backfill commits all at once at the end, so an interrupted build
    leaves the market unchanged and simply runs again on the next start. Jumps are refused (409) while it runs. `clock_state` includes
    `session_open`, `timeline` (bumped on every jump and when history is done, so charts reload)
    and `building_history`.
  - `trading/broker.py`: `Broker`, the cash account. `_build` validates (buying power, shares,
    prices; when replacing, that order's reserved cash or shares count as free). `preview`
    (no side effects: estimated price and total, buying power, cash, position, average cost,
    realized P&L, warnings), `place`, `replace` (cancel/replace: the old order is canceled with
    "Replaced by #N"; the new one has `replaces`), and `cancel`. `_immediate` is the pure "what
    happens right now" check used by both `_activate` and `preview`. `process_iter(t0, t1)` scans each session's
    per-second path (`engine.day_prices`) for every working order and handles events in time
    order. It expires day orders at each close. The ledger (cash, positions, average cost,
    realized P&L) is rebuilt from filled orders and cached. `summary()` gives net liq, buying
    power, day P&L (vs holdings at the previous close) and positions. `rewind(t)` deletes
    orders created after `t` and rolls back later fills, closes and triggers. `reset()`. Fill
    rules are in the module docstring.
  - `trading/orders.py`: the `Order` dataclass. Every status change is time-stamped
    (`created_at`, `triggered_at`, `filled_at`, `closed_at`).
  - `store.py`: SQLite in WAL mode with `synchronous=NORMAL` (a plain commit cost about 60ms on
    disk). A `meta` table holds JSON for `market` (`engine.to_state()` without
    `starts`, saved about every 5s), `market_starts` (written only when
    `engine.starts_version` changes), `clock` and `starting_cash`; an `orders` table holds
    one JSON row per order. Startup
    (`main.load_market`) resumes from it, and `MarketService` rolls back any order newer than
    the saved market time.
  - `api/stream.py`: the WebSocket handler runs its send and receive halves in an **anyio task
    group**. Bare `asyncio.create_task` halves made the tests flaky (about 1 in 5 runs on `main`
    too), because cancellation from the server or test client didn't reach them.
  - `api/`: `GET /api/health`, `GET /api/quotes`, `GET|POST /api/clock` (409 during a jump),
    `GET /api/clock/goto?time=` (preview: forward / rewind / reset / none, plus the snapped
    target), `POST /api/clock/goto {time, future: replay|new}`, WebSocket `/ws`. Times without a
    UTC offset are New York wall-clock time; years 1900–2999; forward jumps are capped at 5
    years. `api/deps.py`: `market` (read-only) and `idle_market` (409 during a jump) for
    anything that syncs or changes state. Trading (`api/trading.py`): `GET /api/account`, `POST /api/account/reset`,
    `GET|POST /api/orders`, `POST /api/orders/preview`, `PATCH /api/orders/{id}` (replace),
    `DELETE /api/orders/{id}`, `GET /api/bars?symbol&interval&end&count`; 422 responses carry a user-readable
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
    `Watchlist` (tick flashes), `QuoteDetail`, `OrderTicket` (form → `OrderReview` from the
    server preview → place; `OrderStatusLine` follows the placed order through the stream,
    including replacements), `BottomTabs` (Positions / Orders with filters, Modify
    (`ModifyOrderDialog`) and Cancel / Activity, plus "Reset account…"),
    `ResetAccountDialog`, `TifSelect`, `Panel`. `lib/orders.ts`: shared status wording
    (`statusText`, `describeOrder`), time-in-force labels, and the replacement lookup.
    Order ids are never reused (`last_order_id` in the store), even after a rewind. The top bar
    shows net liq, buying power and day P&L.
  - `components/PriceChart.tsx`: lightweight-charts v5 (candles + volume histogram on an overlay
    scale, series markers for fills, ET time labels via formatters). Range presets pick a bar
    size and visible range. Older pages load when the visible logical range nears the start.
    Live ticks fold into the last bar (`lib/bars.ts` `bucketStart`/`applyTick`), and the last 2
    bars resync from the server every 1.5s for exact volume. Data loads again when
    `clock.timeline` changes. When older bars are prepended, the visible *dates* are restored
    (the library keeps bar positions, which slid the view back and chained extra loads). Pure
    logic is in `lib/bars.ts`.
  - Indicators: `lib/indicators.ts` (pure math: SMA, EMA, Wilder RSI, MACD, Bollinger with
    population SD, session-anchored VWAP; settings, labels, sanitizing).
    `components/IndicatorLayer.ts` owns the indicator series. Overlays go on pane 0; RSI and MACD
    go in panes 1+ (stretch factor 4:1), and empty panes are removed. The layer recomputes on
    load and updates the last point on each tick. `components/IndicatorMenu.tsx` has toggles and
    lengths, saved in `localStorage` (`paper-trade.indicators`). The legend shows values at the
    hovered bar. Tests use a recording stand-in for
    the library (`src/test/lightweight-charts.mock.ts`, set up in `src/test/setup.ts`).
  - Vite proxies `/api` and `/ws` to `localhost:8000`.
- Tests: backend 163 (pytest), frontend 77 (Vitest + Testing Library). CI runs them on every PR.

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
- Reset (going before the history) starts at the spec prices. A new market always begins at
  the same prices whatever the date.
- `dev.sh` (foreground) still exists alongside `server.sh` (background). Keep it or remove it.
