#!/usr/bin/env bash
# Start backend (port 8000) and frontend (port 5173) together. Ctrl+C stops both.
set -euo pipefail
cd "$(dirname "$0")"
trap 'kill 0' EXIT
(cd backend && uv run uvicorn paper_trade.main:app --host 0.0.0.0 --port 8000 --reload) &
(cd frontend && npm run dev) &
wait
