#!/usr/bin/env bash
# Turn the Paper Trade servers on or off in the background.
#
#   ./server.sh on        start backend + frontend
#   ./server.sh off       stop both
#   ./server.sh restart   stop, then start
#   ./server.sh status    show what's running
#   ./server.sh logs      follow both log files (Ctrl+C to stop following)
#
# Written for the bash 3.2 that ships with macOS.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
RUN="$ROOT/.run"
BACKEND_PORT=8000
FRONTEND_PORT=5173
URL="http://localhost:$FRONTEND_PORT"

mkdir -p "$RUN"

pid_of() { cat "$RUN/$1.pid" 2>/dev/null; }

is_running() {
  local pid
  pid="$(pid_of "$1")"
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

# A process and all of its descendants (uv -> uvicorn -> worker, npm -> vite).
tree_pids() {
  local pid=$1 child
  echo "$pid"
  for child in $(pgrep -P "$pid" 2>/dev/null); do
    tree_pids "$child"
  done
}

# Stop a process tree and wait until it has really exited, so its port is free.
kill_tree() {
  local pids pid alive tries=20
  pids="$(tree_pids "$1")"
  kill $pids 2>/dev/null
  while [ $tries -gt 0 ]; do
    alive=""
    for pid in $pids; do
      kill -0 "$pid" 2>/dev/null && alive="$alive $pid"
    done
    [ -z "$alive" ] && return 0
    sleep 0.5
    tries=$((tries - 1))
  done
  kill -9 $alive 2>/dev/null
}

port_in_use() { lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }

wait_for() {
  local url=$1 tries=60
  while [ $tries -gt 0 ]; do
    curl -sf -o /dev/null "$url" && return 0
    sleep 0.5
    tries=$((tries - 1))
  done
  return 1
}

start_one() {
  local name=$1 port=$2 dir=$3
  shift 3
  if is_running "$name"; then
    echo "  $name: already on (pid $(pid_of "$name"))"
    return 0
  fi
  if port_in_use "$port"; then
    echo "  $name: port $port is already used by another program:"
    lsof -nP -iTCP:"$port" -sTCP:LISTEN | sed 's/^/    /'
    return 1
  fi
  # Detach fully from this terminal: redirect the subshell itself (not just the
  # command) so it doesn't hold our stdout open, and exec so its pid is the server's.
  (cd "$ROOT/$dir" && exec nohup "$@") >"$RUN/$name.log" 2>&1 </dev/null &
  echo $! >"$RUN/$name.pid"
  echo "  $name: starting (log: .run/$name.log)"
}

stop_one() {
  local name=$1
  if is_running "$name"; then
    kill_tree "$(pid_of "$name")"
    echo "  $name: off"
  else
    echo "  $name: was not running"
  fi
  rm -f "$RUN/$name.pid"
}

cmd_on() {
  echo "Turning on Paper Trade…"
  # Install frontend packages on the first run, and again whenever the package list changed.
  if [ ! -f "$ROOT/frontend/node_modules/.package-lock.json" ] ||
    [ "$ROOT/frontend/package-lock.json" -nt "$ROOT/frontend/node_modules/.package-lock.json" ]; then
    echo "  installing frontend packages…"
    (cd "$ROOT/frontend" && npm install --silent) || { echo "npm install failed"; return 1; }
  fi

  start_one backend $BACKEND_PORT backend \
    uv run uvicorn paper_trade.main:app --host 0.0.0.0 --port $BACKEND_PORT --reload || return 1
  start_one frontend $FRONTEND_PORT frontend \
    npm run dev -- --port $FRONTEND_PORT --strictPort || { stop_one backend; return 1; }

  if ! wait_for "http://localhost:$BACKEND_PORT/api/health"; then
    echo "Backend did not come up. Last lines of .run/backend.log:"
    tail -20 "$RUN/backend.log"
    cmd_off >/dev/null
    return 1
  fi
  if ! wait_for "$URL"; then
    echo "Frontend did not come up. Last lines of .run/frontend.log:"
    tail -20 "$RUN/frontend.log"
    cmd_off >/dev/null
    return 1
  fi
  echo "Paper Trade is on: $URL"
}

cmd_off() {
  echo "Turning off Paper Trade…"
  stop_one frontend
  stop_one backend
}

cmd_status() {
  local name
  for name in backend frontend; do
    if is_running "$name"; then
      echo "  $name: on (pid $(pid_of "$name"))"
    else
      echo "  $name: off"
    fi
  done
  is_running frontend && echo "Open $URL"
  return 0
}

case "${1:-}" in
  on | start) cmd_on ;;
  off | stop) cmd_off ;;
  restart) cmd_off && cmd_on ;;
  status) cmd_status ;;
  logs) tail -n 20 -f "$RUN/backend.log" "$RUN/frontend.log" ;;
  *)
    echo "Usage: ./server.sh on | off | restart | status | logs"
    exit 2
    ;;
esac
