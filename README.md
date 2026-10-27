# Paper Trade

A paper trading simulator that runs 24/7 on simulated market data.

- `backend/`: Python + FastAPI (REST under `/api`, WebSocket stream at `/ws`)
- `frontend/`: React + TypeScript + Vite (proxies `/api` and `/ws` to the backend)

## Run

```bash
./dev.sh            # then open http://localhost:5173
```

## Test

```bash
cd backend && uv run pytest
cd frontend && npm test
```

## Roadmap

| Phase | Feature | Status |
|---|---|---|
| 0 | Skeleton: backend, frontend, live connection, layout shell | Done |
| 1 | Market engine: simulated clock, symbols, live quotes, speed control | |
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
