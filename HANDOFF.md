# Handoff

_Last updated: 2026-10-30, after Phase 7 (options chain with Greeks)._

## Where things stand

| Phase | Status | PR |
|---|---|---|
| 0–3. Skeleton, market engine, sessions, time travel, account, orders, order review | Merged; the user confirmed 2a–3 work | #1–#9 |
| CI (GitHub Actions: tests, build, lint on every PR) | Merged | #10 |
| 4. Interactive charts + 5 years of history | Merged | #11 |
| 5. Indicators | Merged | #12 |
| 5.5a. Customizable workspace (dockview): docking, floating windows, snapping, resize, maximize, Panels menu, saved layout | Merged | #13 |
| 5.5b. Collapse panels to edge strips (peek, pin; side panels keep their size) | Merged | #16 |
| 5.5b fixes from the user's testing: chart space (one-line quote, toolbar in the chart's tab bar, thinner bars, full screen), tab-overflow menu while peeking, resize snapping and bring-to-front | Merged | #17 |
| 5.5b redesign from the user's review: chart-first default (side panels collapsed on the left), no tab bars, Panels menu places panels, no date axis, range/bar size at the bottom | Merged | #18 |
| Fixes: divider drags snapping back on market ticks; Reset layout showing nothing after floating | Merged | #19 |
| Fix: with the chart floating, collapsed panels moved to the top/bottom and opened over the whole workspace | Merged | #20 |
| Activity panel removed (Orders' Filled list covers it); filled sells show their realized P/L | Merged | #21 |
| Panels on any edge (left, right, top, bottom), no floating windows; edge tabs open/collapse; Panels menu sets edge and On/Off | Merged | #22 |
| 5.6. More realistic prices (from the user's `improve_sim.docx` discussion) | Merged | #23 |
| 5.7. Engine redesign (performance for 40 symbols and options; user approved Claude's plan) | Merged | #25 |
| 5.8. Sync to real prices (the user chose Alpha Vantage; Stooq now blocks scripts) | Merged | #26 |
| 5.9. Real prices from Alpaca instead of Alpha Vantage (the user chose it over ThetaData and Yahoo) | Merged | #27 |
| Fix: Alpaca certificate checks on macOS (truststore) | Merged | #28 |
| 6. Watchlists and quotes (the user approved the proposal as is) | Merged | #29 |
| Fix: TradingView logo off the chart (user's request); the license's notice and link are a menu item at the bottom of the Panels menu; `NOTICE` holds the notice | Merged | #30 |
| 7. Options chain with Greeks, exchange holidays (the user approved Claude's proposal without trading) | Merged | #31 |
| 8. Option trading, expiration, exercise, assignment | **Next, when the user says so.** | — |

`main` is clean and up to date with GitHub. No open branches or PRs.

## Pick up here

**Waiting on the user** to say when to start Phase 8. Decisions so far (2026-10-30):
- **Layout presets and "narrow screens" (one panel at a time over the chart) are dropped**
  (they were the 5.5c steps). Don't propose them again.
- **Done in 5.7:** 40 symbols, sector factors, volume following moves, spreads widening with
  volatility, single-stock clustering (the realism items discussed after 5.6).
- **Browser performance (discussed with the user):** done in 6: stream subscriptions (only
  on-screen symbols' quotes) and per-row re-renders (`mergeQuotes` keeps unchanged quotes as the
  same object; watchlist rows are memoized). Not done: deltas (at 1× nearly every quote changes
  each tick, so little to gain yet), virtualized long tables, options priced on the server
  (vectorized Black-Scholes; SVI smile) and what-if math in a Web Worker, for Phase 7+. WASM was
  ruled out: the costs are data and DOM, not math.
- **Real prices (5.8, 5.9):** the user asked for prices close to real ones and chose option 2 (sync
  to real closes now, keep the simulation). In 5.9 the user switched the source to Alpaca
  (free, all symbols per request; its free history back to ~2016 can serve Phase 12). Option 3, discussed and deferred to Phase 12: real
  5-year daily history (minutes bridged through real daily bars) plus parameters calibrated from
  it; real days would only replay.
- **Phase 7 (approved proposal, "without trading"):** chain + Greeks + holidays. Option trading
  moved to Phase 8, which the user's approval implies: buy calls/puts and sell to close, covered
  calls and cash-secured puts (a cash account allows no other option sales), positions with
  value/P&L/Greeks, clicking a chain bid/ask to fill the ticket, then expiration, exercise and
  assignment (shares delivered, early assignment). Propose the details before building.
- **Half days (early 1:00 PM closes) were not built** (in the Phase 7 proposal; told the user):
  the engine, 30-minute bars, clock arithmetic and day-order expiry all assume 390-minute
  sessions. Those days are full sessions. Revisit only if the user asks.
- **Later realism:** a VIX-like index and dividends with Phase 8 (dividends change option prices
  and early exercise).
- **Phase 6 decisions (Claude's, within the approved proposal):** the lookup opens on any letter
  except F (full screen's shortcut) or "/"; default lists "Main" (the original 10) and "All
  symbols"; columns and sort are per browser (localStorage), lists are on the server; default
  columns Last, Chg %, Today (sparkline), as the panel is narrow.

**The user is not QA**: verify everything (tests, build, lint with `--deny-warnings`, a
headless-browser check that **looks at screenshots**, green CI) before merging, and don't ask
them to test each phase.

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
  - `market/calendar.py`: sessions are 9:30 AM–4:00 PM ET, Monday–Friday, except NYSE holidays
    (7: `nyse_holidays(year)` with today's rules for every year 1900–3000, Juneteenth from 2022,
    Saturday holidays observed Friday except New Year's Day, Sunday ones Monday; `HOLIDAYS` and a
    numpy `busdaycalendar` passed to every busday call). No half days. `MODEL_VERSION` 4 marks
    the change: older saves continue as a new market and history is rebuilt.
    `snap`, `add_trading_seconds`, `trading_days_between` and so on use numpy business-day
    arithmetic, so they take constant time however far apart the dates are.
  - `market/clock.py`: `SimClock`. Market time = anchor + real elapsed × speed, counted in
    open-market seconds, so the 4:00 PM close continues at the next 9:30 AM. Supports
    pause/resume, `set_speed` and `set_time`. The real-time source is injectable for tests.
  - `market/engine.py`: `MarketEngine` (**redesigned in 5.7, `MODEL_VERSION = 3`**). A session is
    generated in one go at **one-minute resolution**, its `_Skeleton`: each minute's open, high,
    low and close (as log prices) and volume for every symbol, plus the regimes, spread factors and
    loadings. Charts, stored bars, history and day high/low all come from it. **Seconds are
    generated on demand, per symbol** (`_fine(day, m, cols)`, LRU of 64 minutes holding the
    columns generated so far; `_seconds` does the work): the current minute for quotes and
    minutes where an order could fill. A minute's seconds are a bridge through its open, high (at
    the peak of the shared noise), low (at its dip) and close, folded back inside the range, so
    seconds always agree exactly with the bars (`test_seconds_agree_with_minute_bars`); the noise is
    the same market/sector/own mix, so symbols co-move second to second. **How the skeleton is
    made:** unit paths (market factor, one per sector, one per symbol) are drawn at the knots (each
    minute's first and last second) with variances accumulated per second (regime, intraday shape,
    GARCH); symbol log prices = loadings × paths + drift (lognormal-corrected, regime tilt) +
    jumps; each minute's high/low is drawn from the **exact distribution of a Brownian bridge's
    maximum/minimum** given its open and close, shifted for once-a-second sampling
    (Broadie–Glasserman–Kou, 0.5826 σ√Δt), and snapped to the open/close when within half a
    second's move. **Model:** market factor + sector factor (`SymbolSpec.sector`,
    `sector_corr`) + own noise; regimes (scale the market factor more, tilt the drift; the move
    into second r follows the regime of minute (r-1)//60); GJR-GARCH on the market factor's daily
    variance and a GARCH on each symbol's own variance (`own_variance` in `_DayStart`);
    `VARIANCE_RANGE` (0.25, 25) is only a safety net (a cap of 8 biased volatility ~7% low); Student's
    t gaps; quarterly earnings gaps (`_earnings_on`), news jumps **between two minutes** (so no
    jump falls inside a minute), selloffs at the open; intraday volatility shape; volume U-shaped ×
    regime × √variance × activity (squared standardized move, `ACTIVITY_BASE` 0.7) × news burst ×
    earnings; spread factor per minute = market volatility vs normal, clipped to [1, 5]
    (`bid_ask(price, factor)`). Long-run vol and drift match each spec (tests over 10 years).
    **Random draws:** one stream per path keyed by name (`zlib.crc32`), for the skeleton and
    for seconds, so adding a symbol leaves other symbols' prices unchanged (tested) and one
    symbol's seconds can be generated alone. `quotes` is cached per market time. `bid_ask` and
    `bid_ask_arrays` work in whole cents with the same operations, so they agree exactly. **New future:** a new epoch takes over from the first whole minute at or
    after its start (that minute's moves from its open on), so prices up to `t` are unchanged with
    no mid-minute splicing. `backfill_iter(days)` generates history forward from prices of 1.0,
    oldest first, then sets `backfill_scale` and commits the scaled starts. Engine API used
    elsewhere: `quotes`, `minute_bars(day, through)`, `minute_range(day, col)` and
    `first_hit(day, col, lo, hi, candidates, test)` (order matching), `price_at`, `prices_at`,
    `spread_factor(t)`, `day_prices` (slow; tests only). **Saved markets** record `model` and
    `symbols`; `main.load_market` calls `continue_older` when either differs (old 10-symbol saves
    map by `LEGACY_SYMBOLS`; new symbols start at spec prices). `scripts/bench_engine.py [N]`
    benchmarks it. At 40 symbols, 5.6's engine vs 5.7's: fast-forward a year 9.1s → 0.58s,
    5-year history 49s → 3.1s (about 4s in the app including the database), memory 145MB → 7MB,
    a tick 0.09ms either way.
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
    progress messages, via `History.backfill_iter` (it holds the unit-scale 30-minute bars
    and stores them scaled once `backfill_scale` is known). The backfill commits all at once
    at the end, so an interrupted build leaves the market unchanged and simply runs again on
    the next start; a build that needs the backfill clears `bars30` first (old bars from an
    interrupted build or an older model). Jumps are refused (409) while it runs. `clock_state` includes
    `session_open`, `timeline` (bumped on every jump and when history is done, so charts reload)
    and `building_history`.
  - `realprices.py` (5.8; **Alpaca since 5.9**, replacing Alpha Vantage's 25 requests a day): one
    request to `https://data.alpaca.markets/v2/stocks/snapshots?symbols=…&feed=iex` returns all
    symbols (free Basic plan: IEX prices in real time, 200 requests a minute). Price = latest trade,
    else the daily bar's close; trading day from its timestamp in New York. Keys from
    `APCA_API_KEY_ID` and `APCA_API_SECRET_KEY` (environment or `backend/.env`, git-ignored;
    **never commit them or put them in PR text**). Errors become user-readable `FetchError`s
    (401/403 rejected keys, 429, unreachable); some error bodies are HTML and aren't shown.
    `RealPrices` saves prices in the store (`real_prices`: price, trading day, fetched time), the
    last attempt (`real_prices_attempt`) and the last problem (`real_prices_error`, cleared by a
    success; missing symbols are named). `MarketService.refresh_real_prices(older_than)` fetches in
    a worker thread (the store is only touched on the loop thread: SQLite connections are per
    thread) under a lock, so a second caller waits instead of fetching again (the symbol list is
    captured before the request). The background task fetches at startup, then
    `seconds_until_refresh` decides: 15 minutes after the last fetch, or 1 minute after a failed
    one (`real_prices_retry`); it checks the wall clock at least every minute, since a sleeping Mac
    pauses asyncio's clock. GET `/api/real-prices` (the Sync dialog) fetches first when the last
    fetch is over a minute old, waiting at most 5s (the fetch carries on, shielded). `sync_real_prices()` (POST `/api/real-prices/sync`) calls
    `engine.reanchor(prices)`:
    a new market at the current time whose prices **now** equal the targets (previous closes
    scaled by today's simulated move so far), symbols without a real price keep their current
    one; clears and rebuilds the history; keeps the account and orders. New markets (going back
    before the history) start from saved real prices (`real_start_prices`). `tests/conftest.py`
    stops tests from ever reading the keys or calling the service. UI: Go to… → "Sync to real
    prices…" (a footer link) → confirmation with how many symbols have prices and from when.
    Alpaca's response format was checked against the official alpaca-py SDK (the docs site blocks
    scripts). Old store keys `real_prices_usage`/`_attempts`/`_pause` from 5.8 are deleted on load.
  - `market/options.py` (7): `expirations(symbol, now)` (dailies for SPY/QQQ/IWM over 10
    trading days, Friday weeklies 6 weeks, third-Friday monthlies 12 months, January LEAPS 2
    years; holiday → the trading day before; today's drops at the close), `strikes` (standard
    steps by price, doubled past 60 days; ±3 sd, at least ±12%, at most ±0.9 in log terms, ≤160),
    `atm_variance` (today's vol = spec vol × `engine.vol_ratio()` [market GARCH variance × regime
    for the ρ² part, own variance × regime for the rest] reverting to normal at κ=4/yr, plus
    `earnings_move²` per report between tomorrow and expiry: the IV crush is that term vanishing),
    `smile` (SSVI: ρ −0.7 index funds, −0.35 stocks, +0.1 GLD, −0.15 TLT; η 1.1/0.9; γ 0.5),
    `black_scholes` (vectorized; erf via `np.frompyfunc(math.erf)`; theta per day, vega/rho per
    point; RATE 4%, no dividends), `quote_prices` (ticks: pennies for SPY/QQQ/IWM and under $3,
    else nickels; width 1%/2% of price × (1..3 by |Δ|) × (1+0.3 yrs) × the stock's spread
    factor), `activity` (made-up OI anchored to the previous close, steady all day; volume grows
    with the session), `chain(engine, symbol, expiry)` (columns: strikes, call/put lists).
    Engine helpers: `vol_ratio`, `earnings_days(spec, start, end)`. `MarketService.option_chain`
    caches per market time; `GET /api/options/expirations?symbol=`,
    `GET /api/options/chain?symbol=&expiration=`; a stream subscription may add
    `"chain": {symbol, expiration}` and its ticks then carry `chain` (null if no such expiration).
  - `watchlists.py` (6): `Watchlists`, named lists in the store's `watchlists` key (`next_id`,
    `lists` of {id, name, symbols in order}); ids never reused; names tidied, unique ignoring
    case, ≤40 chars; symbols must be known, duplicates dropped; the last list can't be deleted.
    Rewind/reset leave them alone. API (`api/watchlists.py`): `GET /api/symbols`,
    `GET /api/quote-stats` (`MarketService.quote_stats`: `History.quote_stats` cached per
    (market day, timeline); 52-week high/low from `bars30` before today, average volume over the
    last 30 stored sessions; about 130ms on 5 years), `GET /api/sparklines?symbols=` (the engine's
    `day_closes(5)`: closes every 5 minutes so far plus the latest), `GET|POST /api/watchlists`,
    `PATCH|DELETE /api/watchlists/{id}` (PATCH sets name and/or the whole symbol list). Changes
    are broadcast as a `watchlists` stream message (also sent on connect). Saved lists drop
    symbols the market no longer has on load. `bars30_day` index serves the stats (40ms).
  - **Stream subscriptions (6):** a client sends `{"type": "subscribe", "symbols": [...]}`; its
    ticks then carry only those quotes (filtered per connection in `api/stream.py`), and it gets a
    tick straight away (even while paused; not during a jump). `MarketService.send` makes room in
    a full queue by dropping the oldest tick/progress message, so `orders`/`watchlists` arrive.
    The frontend drops quotes from before a time jump (`mergeQuotes(..., fresh)`). The frontend subscribes to the active list, the
    selected symbol and held symbols once lists arrive; until then it gets all.
  - **Quote sizes (6):** `Quote.bid_size`/`ask_size`, round lots from `_quote_sizes`: geometric
    with mean 1 + daily volume / 4M / spread factor, hashed (`_unit_hash`, splitmix64) from the
    minute's seed, the second and each symbol's key, so they're steady within a second and adding
    a symbol leaves others' unchanged. Cosmetic: fills ignore them.
  - `trading/broker.py`: `Broker`, the cash account. `_build` validates (buying power, shares,
    prices; when replacing, that order's reserved cash or shares count as free). `preview`
    (no side effects: estimated price and total, buying power, cash, position, average cost,
    realized P&L, warnings), `place`, `replace` (cancel/replace: the old order is canceled with
    "Replaced by #N"; the new one has `replaces`), and `cancel`. `_immediate` is the pure "what
    happens right now" check used by both `_activate` and `preview`. `process_iter(t0, t1)` finds each working order's next
    event with `engine.first_hit` (minutes whose high/low can't reach the stop or limit are
    skipped; seconds are generated only for candidate minutes) and handles events in time order.
    Bid/ask use the spread factor at that moment (`bid_ask(price, engine.spread_factor(t))`). It expires day orders at each close. The ledger (cash, positions, average cost,
    realized P&L, and each filled sell's realized P&L in `realized_by_order`) is rebuilt from
    filled orders and cached; `order_dict(o)` is how the API and stream send an order (its
    stored fields plus `realized`). `summary()` gives net liq, buying
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
  - `layout/`: the workspace (dockview-react **8.5.1, pinned with `overrides` for dockview and
    dockview-core**). **User's design (2026-10-28): chart first, uncluttered; panels on any
    edge, no floating windows.** The chart is always docked in the middle; Watchlist, Order
    Entry, Options, Positions and Orders (`SIDE_PANELS`, in their order along an edge) each sit
    on an edge: left/right columns (stacked top to bottom) or top/bottom rows (side by side,
    between the columns). Default (`DEFAULT_EDGE`): Watchlist and Order Entry left, Options,
    Positions and Orders bottom, all collapsed. No tab bars (`group.header.hidden` on every group,
    `disableDnd`, `disableFloatingGroups`), so every panel is a group of its own. Each edge
    has a **strip of tabs** for its panels; a tab toggles open/collapsed (it stays as left).
    The **Panels menu** sets each panel's edge (◀ ▶ ▲ ▼) and On/Off (On opens it; a panel
    turned back on returns to its last edge). No date axis on the chart; range and bar size
    along its bottom, Indicators and Latest at the right of the quote line.
    `AppData.tsx`/`appDataContext.ts` hold the stream data and the selected symbol, shared by
    panels. `PanelViews.tsx` has the panel components; each is wrapped in
    `<div class="dock-panel" data-panel="Title">`. `panels.ts`: the registry,
    `buildDefaultLayout`, `panelEdges` (each docked panel's edge, from `edgeOf`), `dockSpot`
    (beside the nearest panel on the same edge in `SIDE_PANELS` order; the first on an edge
    goes at the workspace's left/right edge, or above/below the chart), `placePanel` (an open
    panel moves with `moveTo`, keeping its state; a collapsed one is closed and re-added, as
    dockview can't move hidden groups reliably), `showGroup` (an edge that was all collapsed comes back at its minimum
    thickness, so it gets `EDGE_SIZE`; panels squeezed along an edge share it evenly),
    `edgeTidier`/`collapseEmptyEdges` (an edge whose last shown panel left keeps its space
    in dockview; showing and hiding a collapsed panel there gives it back), `panelPlaces`.
    `Workspace.tsx`: `LayoutProvider` (dockview API for the Panels menu: `places`, `setOn`,
    `setEdge` reading live visibility, `reset`), `Workspace` (restore from `localStorage`
    `paper-trade.layout.v3` (`loadLayout` drops panels that no longer exist), falling back to
    the default when it's bad; panels added to the app since the layout was saved
    (`panelsAddedSinceSave`, from `paper-trade.layout.panels`; layouts from before it had the
    first five) are added collapsed at their default edge, so Options appears for existing
    users; save 300ms after changes and immediately on `pagehide`. Don't
    clear the API ref in effect cleanup: StrictMode's dev double-mount broke saving that way,
    and `App.test` covers it under StrictMode), and `keepSidePanelSizes` (after a structural
    change (a panel added, removed, moved, opened or collapsed) or a workspace resize, each
    open side panel goes back to its edge's thickness: that of a panel already open there
    (panels on an edge share it), else its own last size there (remembered while collapsed),
    else the edge's default; the chart takes the difference. Tracked by panel,
    since moving replaces the group; restored on the next animation frame, because restoring
    inside dockview's layout change got overridden by the rest of it; a saved layout gets its
    sizes back on load via `savedSizes`).
  - **Two dockview traps (#19):** DockviewReact passes any changed *option* prop to
    `updateOptions`, which lays the grid out again from the sizes saved before a divider
    drag. A new function on every render (the shell re-renders on each market tick) snapped
    panels back mid-drag. Keep option props stable and `Workspace` memoized; `App.test`
    checks ticks and the workspace's own re-renders never call `updateOptions`. And
    `api.clear()` keeps the old grid's root, and `fromJSON` could lay out at 0 width:
    `buildDefaultLayout` starts from `fromJSON` of an empty grid, and `onReady` registers
    (before the first load) a relayout at the host's `clientWidth`/`clientHeight` after every
    `fromJSON`.
  - **Edge strips** (`useEdgeStrips.ts`, `EdgeStrip.tsx`, `stripContext.ts`, `edges.ts`): a
    collapsed panel is a hidden grid group (`group.api.setVisible(false)`; dockview saves
    `visible` in the layout JSON, so it survives reloads). `edgeOf` picks the edge from the
    serialized grid (where the group's path and the chart's split). Strips sit in a CSS grid
    around `.dock-host`; Positions and Orders tabs show counts. dockview's
    `onDidLayoutChange` arrives a moment after `setVisible`, so tests `waitFor` it.
  - **Chart space** (the user wants the chart to be the focus; their OS/browser bars already
    take ~100px): top bar 36px (brand, then the clock's date and the connection wording hide
    on narrower windows), one-line `QuoteDetail` (name and `minor` stats hidden by container
    queries on narrow charts), `FullscreenButton` (F). `PriceChart` takes the quote as
    `header` and goes compact (range and bar size as drop-downs) when the buttons don't fit.
  - **Layering:** `.topbar` has z-index 1 over `.workspace` (`isolation: isolate`). Dialogs go
    through `components/Modal.tsx`, a portal to `<body>` at z-index 100. The `className` prop
    doesn't reach an ancestor of the panels, so theme variables are overridden with
    `.workspace .dockview-theme-dark`.
  - `lib/stream.ts`: one WebSocket with auto-reconnect. `useStream()` returns the latest tick
    (clock, account), `quotes` (a `QuoteMap` of every quote seen, merged by `mergeQuotes`), the
    orders list, the watchlists, any fast-forward progress (cleared by the next tick), and
    `subscribe(symbols)` (re-sent after reconnects). Panels read quotes from the map, not
    `tick.quotes` (which only has subscribed symbols). `lib/storage.ts` `useStoredState` for
    localStorage-backed preferences.
  - `components/`: `TopBar` (plus the Panels menu), `ClockControls` (Go to…, pause, speeds from `clock.speeds`,
    regime badge or progress), `GoToDialog` (picker with ±day/week shortcuts → preview → forward
    immediately / rewind confirmation with replay or new future / new-market warning),
    `Watchlist` (6: `ListBar` picker/+/⋯ menu with New/Rename/Delete and Columns; memoized `Row`s
    with tick flashes, held dot, × remove, drag to reorder when unsorted; optimistic `pending`
    order tied to the list object it was based on; pure logic in `lib/watchlist.ts`: `COLUMNS`,
    `sortRows`, `nextSort`, `moveSymbol`), `Sparkline`/`RangeBar` (`lib/sparklines.ts`
    `useSparklines` refetches on a new 5-minute stretch, at most every 2s), `SymbolSearch`
    (combobox with `matchSymbols` from `lib/symbols.ts`), `SymbolLookup` (global: a letter
    other than F or "/" outside fields and dialogs opens it; `lookup` state in AppData),
    `OptionChain` (7: expiration picker, strikes count, ⋯ columns (both sides, saved), calls
    mirrored left, strike, puts; ITM shading; price line, centered when the chain or count
    changes; pure logic in `lib/options.ts`), `OptionsView` (in `PanelViews.tsx`: expirations
    via REST on symbol/day/timeline; `pickExpiration` keeps the picked date or the next;
    `setChainRequest` only while `usePanelShown` (panel and group visible)), `QuoteDetail` (bid/ask with sizes, day range bar, volume, then minor: spread, 52W range, avg
    vol, open, prev close), `OrderTicket` (form → `OrderReview` from the
    server preview → place; `OrderStatusLine` follows the placed order through the stream,
    including replacements), `AccountViews` (Positions with "Reset account…", Orders with filters, Modify
    (`ModifyOrderDialog`) and Cancel, and a P/L column: each order's `realized` from the server,
    the P&L a filled sell realized against the average cost, totalled under the Filled filter;
    the Activity panel was removed at the user's request as it repeated Orders' Filled list),
    `ResetAccountDialog`, `TifSelect`, `Panel`. `lib/orders.ts`: shared status wording
    (`statusText`, `describeOrder`), time-in-force labels, and the replacement lookup.
    Order ids are never reused (`last_order_id` in the store), even after a rewind. The top bar
    shows net liq, buying power and day P&L.
  - `components/PriceChart.tsx`: lightweight-charts v5 (candles + volume histogram on an overlay
    scale, series markers for fills, ET time labels via formatters). Range presets pick a bar
    size and visible range. Older pages load when the visible logical range nears the start.
    Live ticks fold into the last bar (`lib/bars.ts` `bucketStart`/`applyTick`), and the last 2
    bars resync from the server every 1.5s for exact volume. Data loads again when
    `clock.timeline` changes. When older bars are prepended, the visible logical range is
    shifted by the number of bars added. The library keeps bar positions on `setData`, which
    slid the view back in time and chained extra loads. Older pages also wait until a fresh
    load's visible range has been applied (`settled`, about 80ms, because the chart applies
    ranges on its next frame), then load once if the view needs it. Pure
    logic is in `lib/bars.ts`.
  - Indicators: `lib/indicators.ts` (pure math: SMA, EMA, Wilder RSI, MACD, Bollinger with
    population SD, session-anchored VWAP; settings, labels, sanitizing).
    `components/IndicatorLayer.ts` owns the indicator series. Overlays go on pane 0; RSI and MACD
    go in panes 1+ (stretch factor 4:1), and empty panes are removed. The layer recomputes on
    load and updates the last point on each tick. `components/IndicatorMenu.tsx` has toggles and
    lengths, saved in `localStorage` (`paper-trade.indicators`). The legend shows values at the
    hovered bar, from series computed once per bars and settings. `updateLast` recomputes the
    full series each tick: about 3.6ms for all 9 indicators over 5,000 bars, which is fine at
    2 ticks a second. Make it incremental only if that changes. Tests use a recording stand-in for
    the library (`src/test/lightweight-charts.mock.ts`, set up in `src/test/setup.ts`).
  - Vite proxies `/api` and `/ws` to `localhost:8000`.
- Tests: backend 230 (pytest), frontend 145 (Vitest + Testing Library; jsdom gets a ResizeObserver stand-in for dockview). CI runs them on every PR.

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
- No early closes (half days); one-off closures (e.g. national days of mourning) aren't modeled.
- Options: no dividends, a fixed 4% rate, made-up volume/OI; the chain streams to the browser in
  full each tick for one expiration (fine at ~150 strikes).
- Reset (going before the history) starts at the spec prices. A new market always begins at
  the same prices whatever the date.
- Five-year samples vary around the spec vol (persistent volatility); the average over many runs
  matches it.
- Within a minute, seconds are a plausible bridge through the bar's open/high/low/close, not an
  exact Brownian path; minute highs and lows are drawn independently (their joint distribution is
  approximated).
- The watchlist panel doesn't widen when columns are added; it scrolls sideways (drag its edge).
- Changing the symbol list (adding one) still continues the market as a new one (`continue_older`)
  and rebuilds history, although other symbols' prices would be unchanged. Remapping saved
  starts by name and backfilling only the new symbol would avoid that; do it if symbols are added.
- Model-version changes (`continue_older`): the old model's prices can't be recomputed, so
  that session restarts from its previous close with new draws and the quote jumps once
  (positions revalue; a working stop could trigger). Fills made earlier keep their prices,
  but the regenerated history no longer shows them at those prices, and going earlier than
  the continue point resets the account. The user's account had no orders at the 5.6 upgrade.
- History meets the origin with a seam in regime and GARCH variance: the origin's session
  starts normal at variance 1 whatever the history ended in (as before 5.6).
- `dev.sh` (foreground) still exists alongside `server.sh` (background). Keep it or remove it.
