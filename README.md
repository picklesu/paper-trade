# Paper Trade

A paper trading simulator that runs 24/7 on simulated market data.

- `backend/`: Python + FastAPI (REST under `/api`, WebSocket stream at `/ws`)
- `frontend/`: React + TypeScript + Vite (proxies `/api` and `/ws` to the backend)

## How the simulated market works

- Prices are synthetic. Tickers are familiar names, but prices start from fixed values and move by a seeded random model (geometric Brownian motion), so they are not real quotes.
- Symbols move together through a shared market factor, plus their own noise.
- The market switches between **Calm**, **Normal** and **Volatile** regimes (shown in the top bar).
- **Session hours, without the wait:** the market trades 9:30 AM–4:00 PM ET, Monday–Friday. At the close it continues straight from the next session's open (with an overnight gap), so whenever you use the app the market is open. Exchange holidays are not modeled.
- Use the top-bar controls to pause or run at 1×, 10×, 60× or 600×.
- **Go to…** jumps to any date and time:
  - **Forward** fast-forwards the market through the skipped sessions.
  - **Back** asks whether to **replay the same prices** or **generate a new future** from that point. Prices are reproducible, so a replay is exact.
  - **Before the market's history** starts a brand-new market on that date.
- **Everything is saved** in `backend/paper_trade.db`: the market picks up where it left off after a restart (time the server was off doesn't count), along with your account and orders. Delete the file to start completely fresh.

## Workspace

- Every view is a panel: **Chart**, **Watchlist**, **Order Entry**, **Positions**, **Orders**, **Activity**.
- **Drag a tab** to move a panel: drop it on an edge of a panel or the workspace to dock it there (a blue preview shows where), or onto another panel's tabs to group them. Drag the dividers between panels to resize.
- **⧉ pops a panel out** as a floating window over everything else; drag it by its top bar (it snaps to the workspace edges and to other floating windows) and resize it from any edge or corner. **⇲ docks it back.** Shift+drag a tab also floats it.
- **⤢ maximizes** a panel (⤡ restores).
- **⇤ ⇥ ⤒ ⤓ collapse** a docked panel to a strip of tabs on the edge it sits on; the chart takes the space. Click a tab to show that panel; a click anywhere else in the workspace tucks it away again, and the **pin** keeps it open. Side panels keep their size when others collapse or the window resizes; the chart grows and shrinks instead.
- **Panels ▾** (top bar) shows or hides panels and has **Reset layout**. Your layout is saved in this browser.

## Charts

- Interactive candlestick chart with volume: **drag** to pan, **scroll or pinch** to zoom, hover for the bar's open/high/low/close/volume. Older history loads automatically as you scroll or zoom out.
- **Ranges:** 1D, 5D, 1M, 3M, 6M, YTD, 1Y, 5Y, each with a fitting bar size. **Bar sizes:** 1m, 5m, 15m, 30m, 1h, D (daily), W (weekly). 1-minute bars go back 60 sessions, 5- and 15-minute bars a year, and longer bars all the way.
- Your trades are marked on the chart (B/S arrows). The last bar updates live.
- **Indicators** (menu in the chart toolbar; choices are remembered in your browser): SMA 20/50/200, EMA 9/21, VWAP (intraday bars; restarts each session) and Bollinger Bands (20, 2) on the price; RSI (14, with 70/30 lines) and MACD (12, 26, 9) in panes below. Lengths are editable. Hovering a bar shows every indicator's value.
- **Every market comes with 5 years of history** before its start, generated so it leads exactly into the starting prices. It's built in the background the first time (about 10 seconds); "Go to…" can go back into it like any other past date.

## Trading

- **Cash account:** $100,000 to start (change it with **Reset account…**). No margin and no short selling: buys need buying power (cash minus what open buy orders have set aside), and you can only sell shares you own.
- **Order types:** market, limit, stop and stop limit; **Day** (expires at the 4:00 PM close) or **Good till canceled**.
- **Fills:** market orders fill at the ask (buy) or bid (sell). A limit order fills at its price once the quote reaches it (the ask for a buy, the bid for a sell), or at a better price if the market opens past it. A stop triggers when the last price reaches it: a stop then fills at the ask/bid, and a stop limit becomes a limit order. Orders are checked against every second of prices, including at 600× and during fast-forwards.
- **Review before placing:** the ticket shows a preview from the server (estimated price and total, buying power and position before and after, realized P&L on sells, warnings) before the order goes in. Afterwards, the ticket follows the order's live status.
- **Modify** a working order from the Orders tab: it's canceled and replaced by a new order with your changes, as at a real broker. Filter the Orders tab by working, filled, or canceled/expired/rejected.
- **Going back in time** erases trades made after that point and refunds them. Orders that were open then become open again, and positions are valued at that time's prices.

## Run

```bash
./server.sh on        # start backend + frontend in the background, then open http://localhost:5173
./server.sh off       # stop both
./server.sh restart   # stop, then start
./server.sh status    # show what's running
./server.sh logs      # follow the logs (Ctrl+C stops following, not the servers)
```

The servers keep running after you close the terminal. Logs are in `.run/`.
To run in the foreground with logs in the terminal instead, use `./dev.sh`.

## Test

```bash
cd backend && uv run pytest
cd frontend && npm test
```

## Roadmap

| Phase | Feature | Status |
|---|---|---|
| 0 | Skeleton: backend, frontend, live connection, layout shell | Done |
| 1 | Market engine: simulated clock, symbols, live quotes, speed control | Done |
| 2a | Market sessions (9:30–4:00 ET, jump to next open) and date/time control | Done |
| 2b | Persistence, account and orders: market/limit/stop, fills, positions, P&L | Done |
| 3 | Order ticket preview and order history | Done |
| 4 | Candlestick charts | Done |
| 5 | Indicators | Done |
| 5.5 | Customizable workspace: dockable, floating, resizable panels (step 1), collapse to edge strips (step 2) | Done |
| 6 | Watchlists and quotes | |
| 7 | Options chain with Greeks | |
| 8 | Option expiration, exercise, assignment | |
| 9 | Multi-leg strategies and risk graph | |
| 10 | Bracket, OCO, trailing stop orders and alerts | |
| 11 | Analysis tools: screener, portfolio analytics, journal | |
| 12 | Historical replay | |
