#!/usr/bin/env bash
# Disposable real-Mongo integration checks, never an application's configured DB.
# Requires Python venv + submodule; Docker Compose unless TEST_MONGO_URL is explicit.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
PYTHON="${R2A_TEST_PYTHON:-$ROOT/venv/bin/python}"
[ -x "$PYTHON" ] || { echo "Create venv and install backend/requirements-gateway.txt + requirements-dev.txt first" >&2; exit 1; }
[ -f services/arena2api/server.py ] || { echo "Initialise the arena2api submodule first" >&2; exit 1; }

if [ -n "${TEST_MONGO_URL:-}" ]; then
  # Python validates loopback, explicit port, no userinfo/database/options; no .env fallback.
  exec "$PYTHON" backend/tests/integration.py "$@"
fi
command -v docker >/dev/null || { echo "Docker unavailable. Install Docker Compose or supply an explicit loopback TEST_MONGO_URL; no DB tests ran." >&2; exit 1; }
docker compose version >/dev/null
PROJECT="r2a-test-$$-$(date +%s)"
dc() { docker compose --project-name "$PROJECT" --file "$ROOT/compose.integration.yml" "$@"; }
cleanup() { dc down --volumes --remove-orphans >/dev/null 2>&1 || true; }
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

dc up --detach --wait --wait-timeout 90
ADDRESS="$(dc port mongo 27017)"
[[ "$ADDRESS" =~ ^127\.0\.0\.1:[0-9]+$ ]] || { echo "Unexpected test MongoDB binding; refusing to run" >&2; exit 1; }
TEST_MONGO_URL="mongodb://${ADDRESS}/" "$PYTHON" backend/tests/integration.py "$@"
