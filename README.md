# Paper Trade

A paper trading simulator (thinkorswim / Fidelity / Robinhood style) that runs 24/7 on a simulated market. Whenever you open it, the market is open.

- `backend/`: Python + FastAPI (REST under `/api`, WebSocket stream at `/ws`)
- `frontend/`: React + TypeScript + Vite (proxies `/api` and `/ws` to the backend)

## Features

- **Market:** 40 symbols (funds and stocks across 8 sectors) on a seeded random model with shared market and sector factors, calm/normal/volatile regimes, volatility clustering, earnings and news gaps, and spreads and volume that follow volatility. Sessions follow NYSE hours and holidays. Pause or run at 1×, 10×, 60× or 600×.
- **Time travel:** **Go to…** jumps forward (fast-forwarding the market) or back (replay the same prices or generate a new future; later trades are undone and refunded). Dates in the real past replay real daily bars when real data is configured.
- **Workspace:** a chart in the middle with Watchlist, Order Entry, Options, Positions and Orders on collapsible edge panels. **Panels ▾** sets the layout; **F** goes full screen.
- **Charts:** candlesticks with volume, 1D–5Y ranges, 1m–W bars, trade markers, and indicators (SMA, EMA, VWAP, Bollinger, RSI, MACD).
- **Watchlists:** named, sortable lists with configurable columns. Type a ticker (or press **/**) to look one up.
- **Trading:** cash account ($100,000 to start, no margin or shorting). Market, limit, stop and stop-limit orders, Day or GTC, with a server-side preview before placing.
- **Options:** chain with Greeks, priced with Black-Scholes plus a volatility risk premium and smile fitted to real options. Buy/sell to open and close (covered calls, cash-secured puts), exercise, assignment and expiration.
- **Saved state:** everything lives in `backend/paper_trade.db`. Delete it to start fresh.

## Real data (optional)

Without keys, everything works with built-in starting prices. Put keys in `backend/.env` (git ignores it; never commit it):

```
APCA_API_KEY_ID=your-key-id          # Alpaca (free paper account): real quotes and option data
APCA_API_SECRET_KEY=your-secret
ALPHAVANTAGE_API_KEY=your-key        # optional: real earnings dates
```

Then `./server.sh restart`. Real daily history (Tiingo) and economic series (FRED) are downloaded once and saved; **Go to… → Real data… → Update real history** adds the newest days, and **Last real day** jumps to the close of the newest real day the current market replays.

## Run

```bash
./server.sh on        # start backend + frontend in the background, then open http://localhost:5173
./server.sh off       # stop both
./server.sh restart   # stop, then start
./server.sh status    # show what's running
./server.sh logs      # follow the logs (Ctrl+C stops following, not the servers)
```

Logs are in `.run/`. `./dev.sh` runs in the foreground instead.

## Test

```bash
cd backend && uv run pytest
cd frontend && npm test
```

## Roadmap

| Phase | Feature | Status |
|---|---|---|
| 0–5 | Skeleton, market engine, sessions, account and orders, order preview, charts, indicators | Done |
| 5.5–5.9 | Dockable workspace, realistic prices, 40-symbol engine, real quotes via Alpaca | Done |
| 6 | Watchlists and quotes | Done |
| 7 | Options chain with Greeks, exchange holidays; prices calibrated to real options | Done |
| 8a | Option trading, expiration, exercise, assignment | Done |
| 8b | Dividends, American-style pricing, real interest rate, VIX-like index, uncertainty shocks | Done |
| 8c–8d | Real historical start prices and real daily data (Tiingo, FRED) | Done |
| 8e | Replay the real past; volatility and option prices follow the real VIX | Done |
| 8f | A regime-switching future model fitted to the real history | |
| 8g | Walk-forward checks of the future model against what really happened | |
| 9 | Multi-leg strategies and risk graph | |
| 10 | Bracket, OCO, trailing stop orders and alerts | |
| 11 | Analysis tools: screener, portfolio analytics, journal | |
| 12 | Historical replay | |
