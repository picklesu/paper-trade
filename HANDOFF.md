# Handoff

_Last updated: 2026-10-29, after Phase 5.6 (more realistic prices)._

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
| 6. Watchlists and quotes | **Next, when the user says so.** See below. | — |

`main` is clean and up to date with GitHub. No open branches or PRs.

## Pick up here

**Waiting on the user.** Decisions so far (2026-10-29):
- **Layout presets and "narrow screens" (one panel at a time over the chart) are dropped**
  (they were the 5.5c steps). Don't propose them again.
- **On hold:** expanding the symbol list to about 40 across sectors (costs about 4× engine work per
  session, roughly 30s for the first history build).
- **Phase 6 (watchlists and quotes) proposal**, given to the user and not yet approved: several
  named lists saved in SQLite, add/remove/reorder/sort, choosable columns (incl. a day
  sparkline), held-position marker, symbol lookup from anywhere (type a ticker + Enter), a
  fuller quote (day-range bar, 52-week high/low and average volume from history, spread),
  simulated bid/ask sizes.

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
  - `market/calendar.py`: sessions are 9:30 AM–4:00 PM ET, Monday–Friday (no holidays).
    `snap`, `add_trading_seconds`, `trading_days_between` and so on use numpy business-day
    arithmetic, so they take constant time however far apart the dates are.
  - `market/clock.py`: `SimClock`. Market time = anchor + real elapsed × speed, counted in
    open-market seconds, so the 4:00 PM close continues at the next 9:30 AM. Supports
    pause/resume, `set_speed` and `set_time`. The real-time source is injectable for tests.
  - `market/engine.py`: `MarketEngine`. Each session's per-second path (23,400 seconds × 10
    symbols) is generated in one go (`_generate`) from numpy draws seeded by `(seed, date)`
    (`_Draws`), so any moment is reproducible however the clock stepped there. State carried
    between sessions (`_DayStart`): previous close, regime, and the market factor's GARCH
    variance. **The model (Phase 5.6, `MODEL_VERSION = 2`):** a market factor plus each
    symbol's own noise; a per-minute regime chain (calm/normal/volatile) scaling the market
    factor's volatility more than the own noise (so correlation rises in turbulence) and
    tilting the drift (centered); GJR-GARCH(1,1) on the market factor's daily variance
    (0.02/0.16/0.88; `_omega` is corrected for selloffs so it averages 1); Student's t
    overnight gaps (20% of daily variance); jumps: quarterly earnings gaps on a schedule from
    `SymbolSpec.earnings` (`_earnings_on`, 2.5× volume that day), intraday news jumps (2 a year
    per symbol), market selloffs at the open (1 a year, by beta); intraday volatility shape
    (`_intraday_shape`, burst at the open, smaller at the close); U-shaped volume scaled by
    regime and √variance. Jumps take their variance from the continuous part and selloffs'
    mean is added back to the drift, so each symbol's long-run vol and drift match its spec
    (tests check this over 10 years, plus kurtosis, clustering, leverage, correlation and
    earnings gaps). Paths are cached for 4 sessions. A "new future" is a new epoch
    `(start, seed)`: draws after `start` come from the new seed (shocks, news, regime and
    volume draws; the gap's draws come from the epoch in force at the open). The previous close
    is the last price shown (row S-1). The regime shown is the one that drove the latest
    move. `backfill_iter(days)` generates history before the origin **forward, oldest first,
    from prices of 1.0** (so regimes and GARCH carry from day to day), then sets
    `backfill_scale` (origin closes / history's last closes) and commits the scaled starts.
    Paths are multiplicative, so replaying forward gives the same prices, and going back in
    time works the same inside this history. `minute_bars(day, through)` gives OHLCV per
    minute. **A saved market from another model version** can't be regenerated:
    `main.load_market` calls `continue_older`, a new market at the saved time from that
    session's previous closes (account kept; going earlier than that point is "before the
    history", so it resets). Bump `MODEL_VERSION` whenever generated prices change.
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
  - `trading/broker.py`: `Broker`, the cash account. `_build` validates (buying power, shares,
    prices; when replacing, that order's reserved cash or shares count as free). `preview`
    (no side effects: estimated price and total, buying power, cash, position, average cost,
    realized P&L, warnings), `place`, `replace` (cancel/replace: the old order is canceled with
    "Replaced by #N"; the new one has `replaces`), and `cancel`. `_immediate` is the pure "what
    happens right now" check used by both `_activate` and `preview`. `process_iter(t0, t1)` scans each session's
    per-second path (`engine.day_prices`) for every working order and handles events in time
    order. It expires day orders at each close. The ledger (cash, positions, average cost,
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
    Entry, Positions and Orders (`SIDE_PANELS`, in their order along an edge) each sit on an
    edge: left/right columns (stacked top to bottom) or top/bottom rows (side by side, between
    the columns). Default (`DEFAULT_EDGE`): Watchlist and Order Entry left, Positions and
    Orders bottom, all collapsed. No tab bars (`group.header.hidden` on every group,
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
    the default when it's bad; save 300ms after changes and immediately on `pagehide`. Don't
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
    (quotes, clock, account), the orders list, and any fast-forward progress (cleared by the
    next tick).
  - `components/`: `TopBar` (plus the Panels menu), `ClockControls` (Go to…, pause, speeds from `clock.speeds`,
    regime badge or progress), `GoToDialog` (picker with ±day/week shortcuts → preview → forward
    immediately / rewind confirmation with replay or new future / new-market warning),
    `Watchlist` (tick flashes), `QuoteDetail`, `OrderTicket` (form → `OrderReview` from the
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
- Tests: backend 174 (pytest), frontend 105 (Vitest + Testing Library; jsdom gets a ResizeObserver stand-in for dockview). CI runs them on every PR.

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
- Only the market factor's volatility clusters (GARCH), so single stocks show little
  clustering of their own beyond the market's. Five-year samples vary around the spec vol
  (persistent volatility); the average over many runs matches it.
- Model-version changes (`continue_older`): the old model's prices can't be recomputed, so
  that session restarts from its previous close with new draws and the quote jumps once
  (positions revalue; a working stop could trigger). Fills made earlier keep their prices,
  but the regenerated history no longer shows them at those prices, and going earlier than
  the continue point resets the account. The user's account had no orders at the 5.6 upgrade.
- History meets the origin with a seam in regime and GARCH variance: the origin's session
  starts normal at variance 1 whatever the history ended in (as before 5.6).
- `dev.sh` (foreground) still exists alongside `server.sh` (background). Keep it or remove it.
