#!/usr/bin/env bash
# Standalone real arena2api gateway. No MongoDB or app frontend is required.
# This never opens an Arena tab or installs/connects the extension automatically.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
[ -x "$ROOT/venv/bin/python" ] || {
  echo "venv/bin/python missing - create a venv and install backend/requirements-gateway.txt" >&2
  exit 1
}
[ -f "$ROOT/services/arena2api/server.py" ] || {
  echo "arena2api missing - run: git submodule update --init --recursive services/arena2api" >&2
  exit 1
}
exec "$ROOT/venv/bin/python" -m gateway "$@"
