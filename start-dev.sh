#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$ROOT_DIR/.dev-logs"

mkdir -p "$LOG_DIR"

if [[ -f "$ROOT_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env"
  set +a
fi

export PYTHONPATH="$ROOT_DIR/apps:$ROOT_DIR/apps/backend${PYTHONPATH:+:$PYTHONPATH}"
export GPU_WORKER_URL="${GPU_WORKER_URL:-http://localhost:8001}"

PIDS=()

check_port_free() {
  local port="$1"
  if command -v fuser >/dev/null 2>&1 && fuser -n tcp "$port" >/dev/null 2>&1; then
    echo "port $port is already in use; stop the existing service before running this script" >&2
    exit 1
  fi
}

cleanup() {
  trap - INT TERM EXIT
  echo
  echo "stopping fiebatt services..."
  for pid in "${PIDS[@]:-}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}

trap cleanup INT TERM EXIT

start_service() {
  local name="$1"
  shift
  echo "starting $name..."
  "$@" >"$LOG_DIR/$name.log" 2>&1 &
  local pid=$!
  PIDS+=("$pid")
  echo "  $name pid=$pid log=$LOG_DIR/$name.log"
}

if [[ ! -x "$ROOT_DIR/apps/backend/.venv/bin/uvicorn" ]]; then
  echo "missing backend environment: apps/backend/.venv/bin/uvicorn" >&2
  exit 1
fi

if [[ ! -x "$ROOT_DIR/apps/gpu-worker/.venv/bin/uvicorn" ]]; then
  echo "missing GPU worker environment: apps/gpu-worker/.venv/bin/uvicorn" >&2
  exit 1
fi

check_port_free 3001
check_port_free 8000
check_port_free 8001

start_service frontend npm --prefix "$ROOT_DIR/apps/web" run dev

start_service backend \
  "$ROOT_DIR/apps/backend/.venv/bin/uvicorn" \
  app.main:app \
  --reload \
  --host 0.0.0.0 \
  --port 8000

start_service sam-worker bash -c "cd '$ROOT_DIR/apps/gpu-worker' && exec .venv/bin/uvicorn main:app --host 0.0.0.0 --port 8001"

echo
echo "fiebatt services started"
echo "  frontend:   http://localhost:3001"
echo "  backend:    http://localhost:8000"
echo "  sam worker: http://localhost:8001"
echo "  logs:       $LOG_DIR"
echo
echo "press Ctrl-C to stop all services"

while true; do
  for i in "${!PIDS[@]}"; do
    if ! kill -0 "${PIDS[$i]}" 2>/dev/null; then
      echo "service pid ${PIDS[$i]} exited; check logs in $LOG_DIR" >&2
      exit 1
    fi
  done
  sleep 2
done
