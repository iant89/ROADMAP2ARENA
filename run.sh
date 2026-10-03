#!/usr/bin/env bash
# Local development: app backend :8001, Vite :5173.
# STUB=1 starts the canned arena stand-in :9090; GATEWAY=1 starts real arena2api.
# Otherwise use an already-running external gateway. STUB/GATEWAY are mutually exclusive.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

STUB="${STUB:-0}"
GATEWAY="${GATEWAY:-0}"
for value in "$STUB" "$GATEWAY"; do
  case "$value" in 0|1) ;; *) echo "STUB and GATEWAY must be 0 or 1" >&2; exit 1 ;; esac
done
if [ "$STUB" = "1" ] && [ "$GATEWAY" = "1" ]; then
  echo "Choose STUB=1 OR GATEWAY=1, not both (they share the gateway port)" >&2
  exit 1
fi

PIDS=()
cleanup() {
  trap - INT TERM EXIT
  for pid in "${PIDS[@]:-}"; do
    if [ -n "$pid" ]; then kill "$pid" 2>/dev/null || true; fi
  done
  wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

# Validate before spawning anything, so failed setup does not leave a partial stack.
if [ -f backend/server.py ]; then
  [ -x venv/bin/uvicorn ] || { echo "venv/bin/uvicorn missing - create ./venv and install dependencies" >&2; exit 1; }
  [ -f backend/.env ] || { echo "backend/.env missing - copy backend/.env.example and adjust" >&2; exit 1; }
fi
if [ "$GATEWAY" = "1" ]; then
  ./run-gateway.sh --check >/dev/null
fi

if [ "$STUB" = "1" ]; then
  (cd backend && exec ../venv/bin/uvicorn tests.arena_stub:app --host 127.0.0.1 --port 9090) &
  PIDS+=("$!")
  echo "arena2api stand-in (canned responses): http://127.0.0.1:9090"
elif [ "$GATEWAY" = "1" ]; then
  ./run-gateway.sh &
  PIDS+=("$!")
  echo "real arena2api started - install/connect its browser extension separately"
fi

if [ -f backend/server.py ]; then
  if ! (exec 3<>/dev/tcp/127.0.0.1/27017) 2>/dev/null; then
    echo "warning: nothing listening on 127.0.0.1:27017 - start/configure MongoDB first" >&2
  fi
  # Repos stay outside backend/. Excludes need watchfiles installed (F-006).
  (cd backend && exec ../venv/bin/uvicorn server:app --host 127.0.0.1 --port 8001 --reload \
    --reload-dir "$PWD" --reload-exclude "$PWD/tests" \
    --reload-exclude 'tests/*' --reload-exclude 'data/*' --reload-exclude '*.git*') &
  PIDS+=("$!")
  echo "backend:  http://127.0.0.1:8001/api"
else
  echo "backend/server.py not found - starting frontend only"
fi

# Browser requests stay same-origin; Vite proxies /api to the backend server-side.
# For a remote/live preview set R2A_DEV_HOST=0.0.0.0, never a browser localhost API URL.
DEV_HOST="${R2A_DEV_HOST:-127.0.0.1}"
(cd frontend && VITE_BACKEND_URL='' exec yarn dev --host "$DEV_HOST" --port 5173) &
PIDS+=("$!")
echo "frontend: http://${DEV_HOST}:5173"

# Any crashed/exited service tears down the rest rather than leaving a broken stack.
set +e
wait -n "${PIDS[@]}"
status=$?
set -e
exit "$status"
