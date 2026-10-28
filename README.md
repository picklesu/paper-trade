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

## Trading

- **Cash account:** $100,000 to start (change it with **Reset account…**). No margin and no short selling: buys need buying power (cash minus what open buy orders have set aside), and you can only sell shares you own.
- **Order types:** market, limit, stop and stop limit; **Day** (expires at the 4:00 PM close) or **Good till canceled**.
- **Fills:** market orders fill at the ask (buy) or bid (sell). A limit order fills at its price when the market reaches it, or at a better price if the market opens past it. A stop triggers when the last price reaches it: a stop then fills at the ask/bid, and a stop limit becomes a limit order. Orders are checked against every second of prices, including at 600× and during fast-forwards.
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
| 3 | Order ticket preview and order history | |
| 4 | Candlestick charts | |
| 5 | Indicators | |
| 6 | Watchlists and quotes | |
| 7 | Options chain with Greeks | |
| 8 | Option expiration, exercise, assignment | |
| 9 | Multi-leg strategies and risk graph | |
| 10 | Bracket, OCO, trailing stop orders and alerts | |
| 11 | Analysis tools: screener, portfolio analytics, journal | |
| 12 | Historical replay | |
