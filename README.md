# Paper Trade

A paper trading simulator that runs 24/7 on simulated market data.

- `backend/`: Python + FastAPI (REST under `/api`, WebSocket stream at `/ws`)
- `frontend/`: React + TypeScript + Vite (proxies `/api` and `/ws` to the backend)

## How the simulated market works

- Prices are synthetic. Tickers are familiar names, but prices start from fixed values and move by a seeded random model, so they are not real quotes. Each symbol's long-run volatility and drift match its profile.
- **40 symbols:** 8 funds (SPY, QQQ, IWM, sector funds XLF/XLE/XLV, gold GLD and Treasuries TLT) and 32 stocks across 8 sectors.
- Symbols move together through a shared market factor, a sector factor (oil stocks with oil stocks, banks with banks) and their own noise. In turbulent markets they move together more; Treasuries tend to rise when stocks fall.
- The market switches between **Calm**, **Normal** and **Volatile** regimes (shown in the top bar). Volatile spells lean down, calm ones up.
- **Volatility clusters:** a big day, a drop especially, is followed by turbulent days that calm down gradually. A stock stays jumpy for a while after its own big moves (earnings, news) too.
- **Fat tails:** overnight gaps are occasionally large; stocks gap on **earnings** four times a year (on a fixed quarterly schedule, with heavier volume that day), jump on news now and then, and the whole market sometimes sells off at the open.
- Within a day, prices move most just after the open and pick up again into the close. **Volume** is heavier on big moves and after news, and **spreads widen** when the market is volatile (at the open, in volatile spells, after selloffs).
- **Session hours, without the wait:** the market trades 9:30 AM–4:00 PM ET, Monday–Friday, except NYSE holidays (New Year's Day, MLK Day, Presidents' Day, Good Friday, Memorial Day, Juneteenth, Independence Day, Labor Day, Thanksgiving, Christmas). At the close it continues straight from the next session's open (with an overnight gap), so whenever you use the app the market is open. Early closes (1:00 PM around some holidays) are full sessions here.
- Use the top-bar controls to pause or run at 1×, 10×, 60× or 600×.
- **Real prices:** with free Alpaca API keys (see [Real prices](#real-prices)), **Go to… → Sync to real prices…** moves the market to the latest real closes and continues from there; new markets start from them too. The simulation moves prices randomly afterwards, so they drift from real ones over time.
- **Go to…** jumps to any date and time:
  - **Forward** fast-forwards the market through the skipped sessions.
  - **Back** asks whether to **replay the same prices** or **generate a new future** from that point. Prices are reproducible, so a replay is exact.
  - **Before the market's history** starts a brand-new market on that date.
- **Everything is saved** in `backend/paper_trade.db`: the market picks up where it left off after a restart (time the server was off doesn't count), along with your account and orders. Delete the file to start completely fresh.

## Workspace

- Every view is a panel: **Chart**, **Watchlist**, **Order Entry**, **Options**, **Positions**, **Orders**. Panels have no tab bars or buttons, so the chart gets the room.
- **The chart is always in the middle; every other panel sits on an edge**: left, right, top or bottom. By default Watchlist and Order Entry are on the left and Positions and Orders on the bottom, all collapsed, so the chart has the screen.
- Each edge has a strip of **tabs**, one per panel on it (Positions and Orders show a count when they have something). A tab **opens** its panel or **collapses** it again; it stays as you left it. Panels open on the same edge stack: top to bottom on the left and right, side by side on the top and bottom.
- **Panels ▾** (top bar) puts each panel on an edge (◀ ▶ ▲ ▼) and turns it **On** or **Off**. **Reset layout** goes back to the default. Your layout is saved in this browser.
- Drag the dividers to resize; the chart grows and shrinks, and the panels on the edges keep their size when others open, close or move and when the window resizes.
- The chart: the quote on one line with **Indicators ▾** and **Latest ⇥** at its right, and the range and bar size along the bottom (as drop-downs when the chart is narrow). There's no date axis: the line above the candles shows the hovered (or latest) bar's date and time.
- **⛶ Full screen** (top bar, or press **F**) hides the OS and browser bars so the chart gets the whole screen; Esc leaves. Another option: in Chrome, ⋮ → Cast, save, and share → Install page as app (or Create shortcut → Open as window) gives the app its own window without tabs or bookmarks.

## Watchlists and quotes

- **Several named watchlists**, saved with the market: pick one in the Watchlist panel; **+** adds a symbol (type a ticker or part of a name); hover a row and click **×** to remove it; **drag rows** to reorder. **⋯** creates, renames and deletes lists.
- **Sort** by clicking a column header (high to low, low to high, then back to your own order). **Choose columns** in **⋯ → Columns**: last, change, change %, today's chart (a sparkline against the previous close), day range, bid, ask, bid/ask sizes, spread, volume, average volume (30 days), 52-week high/low and shares held. A dot next to a symbol means you hold it.
- **Look up a symbol from anywhere:** just start typing a ticker (or press **/**), then **Enter** shows it in the chart and order entry. Clicking the symbol in the quote line does the same. (F stays full screen.)
- **The quote line** shows bid and ask with their sizes, the day's range as a bar, volume, spread, the 52-week range and average volume (from the history), open and previous close. What doesn't fit a narrow chart is dropped from the right.
- The browser only receives quotes for what's on screen (the watchlist, the selected symbol and your positions), and only rows whose quote changed are redrawn.

## Options

- The **Options** panel (bottom edge by default; open it from its tab) shows the selected symbol's **option chain**: calls on the left, puts on the right, strikes in the middle, in-the-money options shaded and a line at the current price. It follows the symbol you pick anywhere in the app.
- **Expirations:** with real option data (see below), each symbol lists the expirations it really has, as a pattern laid over the simulated date: for example AAPL's Monday and Wednesday options for two weeks, SPY's dailies and month- and quarter-ends, and June and December LEAPS. Without it: SPY, QQQ and IWM have daily expirations for two weeks; every symbol has Friday weeklies for six weeks, monthlies (third Friday) for a year and January expirations two years out. When a Friday is a holiday, that week's options expire on Thursday. The picker shows days left and the kind (D daily, W weekly, Q month- or quarter-end, M monthly).
- **Columns** (⋯, saved in your browser, the same on both sides): bid, mid (halfway between bid and ask), ask, bid and ask sizes, implied volatility, delta, gamma, theta (per day), vega (per volatility point), rho, volume and open interest. Choose how many strikes to show around the price.
- **How prices are made:** Black-Scholes at a 4% rate with no dividends. The **level** of implied volatility is the simulation's own: the stock's current volatility (higher in turbulent markets, nudged by how much it has moved so far today), drifting back to normal for longer expirations; an expiration that spans one of the simulation's earnings dates carries the earnings move, so its implied volatility drops the day after the report. That is multiplied by the **volatility risk premium**: real options cost more than the volatility that follows (about 1.0–1.25× by symbol and expiration, from real data; 1.12× without), so selling options has its realistic edge on average. The **shape** around it (how much dearer out-of-the-money puts are, how steep the smile is, by expiration) is fitted to each symbol's real options once a day; without real data it uses built-in estimates. Within the day, each strike keeps its volatility as the stock moves (so a falling stock moves onto the pricier puts' side), and the skew steepens in a selloff. Bids and asks sit on penny or nickel ticks, wider far from the money, for long expirations, in the first minutes after the open and in the days before earnings. Volume and open interest follow each symbol's real mix of expirations, strikes, calls and puts, scaled to the simulated day's trading (made up without real data); bid and ask sizes are for show.
- The header shows the at-the-money implied volatility, the **expected move** by expiration (one standard deviation), and whether the smile is **calibrated** to real options and from which day; hover over it for the fitted premium and the real implied earnings move.
- **Trading options:** click a bid (to sell), an ask (to buy) or a mid (keeping the side you had) in the chain: Order Entry opens on its **Options** tab with that contract and price, or pick the expiration, strike and call/put there yourself. Quantities are contracts of 100 shares; prices are per share. Market and limit orders, day or good till canceled. A limit fills when the chain's ask (buy) or bid (sell) reaches it, checked every second; a limit **between the bid and ask** (such as the mid) can also fill when someone trades with it, more often the nearer it is to the other side and the busier the contract (at the mid on an active contract, usually within a minute or two; on a quiet one, hours). The review says how long that typically takes. No commissions.
  - **Buy to open** calls and puts, and **sell to close** them.
  - **Sell to open** only what a cash account allows: a **covered call** (100 shares you own per contract, locked until the call is closed, expires or is assigned) or a **cash-secured put** (the strike × 100 is set aside from buying power). **Buy to close** either.
  - The review shows the breakeven at expiration, the most you can lose (or make), and the cash or shares set aside.
- **Positions** shows options with their mid, P&L, delta (in shares) and theta (dollars a day), with totals. **Exercise…** exercises long options early (if you have the cash for a call's shares, or the shares for a put); it warns when selling would get more.
- **Expiration** (4:00 PM on the expiration day): options $0.01 or more in the money are exercised (yours) or assigned (ones you sold); the rest expire worthless. A long call your cash can't cover, or a long put without the shares to deliver, is sold at the bid at 3:59 PM instead, as brokers do. Short options deep in the money with almost no time value left can be **assigned early** at any close (as in real markets). Orders lists each exercise, assignment and expiration with its P/L: the option closes at its value and the shares trade at the stock's price, which together is buying or selling the shares at the strike.

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
- **Orders** lists every order with filters (All, Working, Filled, Canceled / expired / rejected). A closing trade (a sell, or buying back an option you sold) shows the **P/L** it realized against your average price; the Filled filter adds them up. (An opening trade's open P/L is in Positions.)
- **Review before placing:** the ticket shows a preview from the server (estimated price and total, buying power and position before and after, realized P&L on sells, warnings) before the order goes in. Afterwards, the ticket follows the order's live status.
- **Modify** a working order from the Orders tab: it's canceled and replaced by a new order with your changes, as at a real broker. Filter the Orders tab by working, filled, or canceled/expired/rejected.
- **Going back in time** erases trades made after that point and refunds them. Orders that were open then become open again, and positions are valued at that time's prices.

## Real prices

Real prices come from [Alpaca](https://alpaca.markets/)'s market data API. Its free keys come with a free paper trading account (no money needed): sign up, open the paper trading dashboard and generate API keys. Put them in `backend/.env`, which git ignores (never commit it):

```
APCA_API_KEY_ID=your-key-id
APCA_API_SECRET_KEY=your-secret
```

The same keys bring **real option data**: once a day (at startup when the last is over 20 hours old) the server fetches each symbol's option chain from Alpaca's free indicative feed and its open interest, plus a year of daily bars, fits the option model to them and saves the result; that takes about a minute and a half for all 40 symbols, in the background, and the Sync dialog shows how far it got. With an [Alpha Vantage](https://www.alphavantage.co/support/#api-key) key (`ALPHAVANTAGE_API_KEY=your-key` in the same file; free) it also gets real earnings dates, so the fits leave out the expirations around a report and measure the real implied earnings move. Alpaca's free option quotes are approximate: they're used for the smile's shape, not for bid/ask spreads, which stay the model's (`backend/scripts/compare_option_spreads.py` compares them with Cboe's public quotes).

Then `./server.sh restart`. The server fetches all 40 symbols in one request when it starts and every 15 minutes; opening **Go to… → Sync to real prices…** fetches again if the last fetch is over a minute old. The free plan's real-time prices come from the IEX exchange, within cents of the consolidated price. Syncing moves every symbol with a real price to it; any without one keep their current price. Without keys, everything works as before with built-in starting prices.

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
| 5.5 | Customizable workspace: dockable, floating, resizable panels (step 1), collapse to edge strips (step 2), chart-first layout with the Panels menu (step 2b), panels on any edge (step 2c) | Done |
| 5.6 | More realistic prices: fat tails (gaps, earnings, news, selloffs), volatility clustering with the leverage effect, intraday volatility shape | Done |
| 5.7 | Engine redesign: minute-resolution sessions with seconds on demand (16× faster, 20× less memory), 40 symbols with sectors, volume and spreads that follow volatility, single-stock clustering | Done |
| 5.8 | Sync to real prices | Done |
| 5.9 | Real prices from Alpaca: all symbols in one request, refreshed every 15 minutes (replaces Alpha Vantage's 25 requests a day) | Done |
| 6 | Watchlists and quotes: named lists, columns, sorting, sparklines, symbol lookup, fuller quote | Done |
| 7 | Options chain with Greeks, exchange holidays | Done |
| 7.5 | Option prices calibrated to real markets: each symbol's smile, volatility risk premium, expirations, volume and open interest from its real options, once a day | Done |
| 8a | Option trading: buy/sell to open and close (covered calls, cash-secured puts), positions with Greeks, expiration, exercise, assignment | Done |
| 8b | Dividends (and early exercise before them), American-style pricing, the real interest rate, a VIX-like index | |
| 9 | Multi-leg strategies and risk graph | |
| 10 | Bracket, OCO, trailing stop orders and alerts | |
| 11 | Analysis tools: screener, portfolio analytics, journal | |
| 12 | Historical replay | |
