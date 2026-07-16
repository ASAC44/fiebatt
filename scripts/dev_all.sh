#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT_DIR/.dev-logs"
FRONTEND_DIR="$ROOT_DIR/apps/frontend"
BACKEND_DIR="$ROOT_DIR/apps/backend"
VISION_WORKER_DIR="$ROOT_DIR/apps/vision-worker"

mkdir -p "$LOG_DIR"

if [[ -f "$ROOT_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env"
  set +a
fi

export PYTHONPATH="$ROOT_DIR/apps/backend${PYTHONPATH:+:$PYTHONPATH}"
export VISION_WORKER_URL="${VISION_WORKER_URL:-${GPU_WORKER_URL:-http://localhost:8001}}"

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

wait_for_service() {
  local name="$1"
  local url="$2"
  local attempts="${3:-60}"

  for ((attempt = 1; attempt <= attempts; attempt++)); do
    if curl --silent --fail --max-time 2 "$url" >/dev/null; then
      echo "  $name ready: $url"
      return 0
    fi
    sleep 1
  done

  echo "$name did not become ready at $url" >&2
  echo "last $name log lines:" >&2
  tail -n 30 "$LOG_DIR/$name.log" >&2 || true
  return 1
}

if [[ ! -x "$BACKEND_DIR/.venv/bin/python" ]] || \
  ! "$BACKEND_DIR/.venv/bin/python" -c "import uvicorn" >/dev/null 2>&1; then
  echo "missing backend environment: apps/backend/.venv with uvicorn installed" >&2
  exit 1
fi

if [[ ! -x "$VISION_WORKER_DIR/.venv/bin/python" ]] || \
  ! "$VISION_WORKER_DIR/.venv/bin/python" -c "import uvicorn" >/dev/null 2>&1; then
  echo "missing vision worker environment: apps/vision-worker/.venv with uvicorn installed" >&2
  exit 1
fi

if [[ ! -x "$FRONTEND_DIR/node_modules/.bin/next" ]]; then
  echo "missing frontend dependencies: run 'npm install --prefix apps/frontend'" >&2
  exit 1
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "curl is required for local service readiness checks" >&2
  exit 1
fi

check_port_free 3001
check_port_free 8000
check_port_free 8001

start_service frontend npm --prefix "$FRONTEND_DIR" run dev

start_service backend \
  "$BACKEND_DIR/.venv/bin/python" -m uvicorn \
  app.main:app \
  --reload \
  --reload-dir "$BACKEND_DIR/app" \
  --host 0.0.0.0 \
  --port 8000

start_service vision-worker bash -c "cd '$VISION_WORKER_DIR' && exec .venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 8001"

echo
echo "waiting for services..."
wait_for_service vision-worker http://localhost:8001/health
wait_for_service backend http://localhost:8000/api/health
wait_for_service frontend http://localhost:3001

echo
echo "fiebatt services started"
echo "  frontend:      http://localhost:3001"
echo "  backend:       http://localhost:8000"
echo "  vision worker: http://localhost:8001"
echo "  logs:          $LOG_DIR"
echo
echo "press Ctrl-C to stop all services"

while true; do
  for i in "${!PIDS[@]}"; do
    if ! kill -0 "${PIDS[$i]}" 2>/dev/null; then
      echo "service pid ${PIDS[$i]} exited; recent logs:" >&2
      tail -n 30 "$LOG_DIR"/*.log >&2 || true
      exit 1
    fi
  done
  sleep 2
done
