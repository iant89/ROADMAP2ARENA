#!/usr/bin/env bash
# Local development: backend (if present) on 127.0.0.1:8001 and Vite on 5173.
# Ctrl+C stops both.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

PIDS=()
cleanup() {
  trap - INT TERM EXIT
  for pid in "${PIDS[@]:-}"; do
    if [ -n "$pid" ]; then kill "$pid" 2>/dev/null || true; fi
  done
  wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

if [ -f backend/server.py ]; then
  [ -x venv/bin/uvicorn ] || { echo "venv/bin/uvicorn missing - run ./setup.sh or create ./venv first" >&2; exit 1; }
  (cd backend && exec ../venv/bin/uvicorn server:app --host 127.0.0.1 --port 8001 --reload) &
  PIDS+=("$!")
  echo "backend:  http://127.0.0.1:8001"
else
  echo "backend/server.py not found - starting frontend only"
fi

(cd frontend && VITE_BACKEND_URL=http://127.0.0.1:8001 exec yarn dev --port 5173) &
PIDS+=("$!")
echo "frontend: http://127.0.0.1:5173"

wait
