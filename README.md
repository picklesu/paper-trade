# Paper Trade

A paper trading simulator that runs 24/7 on simulated market data.

- `backend/`: Python + FastAPI (REST under `/api`, WebSocket stream at `/ws`)
- `frontend/`: React + TypeScript + Vite (proxies `/api` and `/ws` to the backend)

## How the simulated market works

- Prices are synthetic. Tickers are familiar names, but prices start from fixed values and move by a seeded random model (geometric Brownian motion), so they are not real quotes.
- Symbols move together through a shared market factor, plus their own noise.
- The market switches between **Calm**, **Normal** and **Volatile** regimes (shown in the top bar).
- The market clock runs 24/7 in New York time. Use the top-bar controls to pause or run at 1×, 10×, 60× or 600×. A new trading day (open, high, low, volume reset) starts at midnight ET.
- Restarting the backend restarts the market from the starting prices.

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
| 2 | Account and orders: market/limit/stop, fills, positions, P&L | |
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
