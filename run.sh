#!/usr/bin/env bash
# Local development: backend (if present) on 127.0.0.1:8001 and Vite on 5173.
# Optional: STUB=1 also starts the local arena2api stand-in on 127.0.0.1:9090
# (backend/tests/arena_stub.py - NOT the real arena2api). Ctrl+C stops all.
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
  [ -f backend/.env ] || { echo "backend/.env missing - copy backend/.env.example and adjust" >&2; exit 1; }
  if ! (exec 3<>/dev/tcp/127.0.0.1/27017) 2>/dev/null; then
    echo "warning: nothing listening on 127.0.0.1:27017 - start MongoDB first" >&2
  fi
  if [ "${STUB:-0}" = "1" ]; then
    (cd backend && exec ../venv/bin/uvicorn tests.arena_stub:app --host 127.0.0.1 --port 9090) &
    PIDS+=("$!")
    echo "arena2api stub: http://127.0.0.1:9090"
  fi
  # Watch only the backend source: job repos (R2A_DATA_DIR, default ./data) and tests/ must never
  # trigger a reload - that would kill a running job (F-006). Excludes need watchfiles installed.
  (cd backend && exec ../venv/bin/uvicorn server:app --host 127.0.0.1 --port 8001 --reload \
    --reload-dir "$PWD" --reload-exclude "$PWD/tests" \
    --reload-exclude 'tests/*' --reload-exclude 'data/*' --reload-exclude '*.git*') &
  PIDS+=("$!")
  echo "backend:  http://127.0.0.1:8001/api"
else
  echo "backend/server.py not found - starting frontend only"
fi

(cd frontend && VITE_BACKEND_URL=http://127.0.0.1:8001 exec yarn dev --port 5173) &
PIDS+=("$!")
echo "frontend: http://127.0.0.1:5173"

wait
