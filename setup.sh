#!/usr/bin/env bash
# Production-style setup for this app on a fresh Ubuntu server.
# Idempotent: safe to re-run. Usage: ./setup.sh (env: APP_NAME, APP_PORT, APP_URL, FORCE=1)
# Opt in to the included real gateway with GATEWAY=1 (browser extension remains manual).
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
APP_DIR="$(pwd)"

APP_NAME="${APP_NAME:-$(basename "$APP_DIR")}"
APP_PORT="${APP_PORT:-80}"
SERVER_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
SERVER_IP="${SERVER_IP:-127.0.0.1}"
if [ -z "${APP_URL:-}" ]; then
  if [ "$APP_PORT" = "80" ]; then APP_URL="http://${SERVER_IP}"; else APP_URL="http://${SERVER_IP}:${APP_PORT}"; fi
fi
FORCE="${FORCE:-0}"
RUN_USER="${SUDO_USER:-$(id -un)}"
GATEWAY="${GATEWAY:-0}"

log() { printf '\n==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- safety checks
case "$GATEWAY" in 0|1) ;; *) die "GATEWAY must be 0 or 1" ;; esac
if [ "$GATEWAY" = "1" ] && [ ! -f services/arena2api/server.py ]; then
  die "arena2api missing - run: git submodule update --init --recursive services/arena2api"
fi
if [ ! -r /etc/os-release ] || ! grep -qi '^ID=ubuntu' /etc/os-release; then
  [ "$FORCE" = "1" ] || die "This script supports Ubuntu only (set FORCE=1 to override)."
fi
if [ "$(id -u)" -eq 0 ] && [ -z "${SUDO_USER:-}" ]; then
  [ "$FORCE" = "1" ] || die "Run as a regular user with sudo, not as root (set FORCE=1 to override)."
fi
if [ -e /etc/axiom-workspace ]; then
  [ "$FORCE" = "1" ] || die "/etc/axiom-workspace exists - this looks like a dev workspace (set FORCE=1 to override)."
fi

SUDO=""
if [ "$(id -u)" -ne 0 ]; then SUDO="sudo"; fi

# ---------------------------------------------------------------- base packages
log "Base packages"
BASE_PKGS="curl git build-essential python3 python3-venv python3-pip rsync gnupg ca-certificates"
MISSING=""
for pkg in $BASE_PKGS; do
  if dpkg -s "$pkg" >/dev/null 2>&1; then
    echo "$pkg already installed"
  else
    MISSING="$MISSING $pkg"
  fi
done
if [ -n "$MISSING" ]; then
  $SUDO apt-get update -y
  # shellcheck disable=SC2086
  $SUDO DEBIAN_FRONTEND=noninteractive apt-get install -y $MISSING
fi

# ---------------------------------------------------------------- Node.js >= 22
log "Node.js"
NODE_MAJOR=0
if command -v node >/dev/null 2>&1; then
  NODE_MAJOR="$(node -v | sed 's/^v//' | cut -d. -f1)"
fi
if [ "$NODE_MAJOR" -ge 22 ]; then
  echo "node $(node -v) already installed"
else
  curl -fsSL https://deb.nodesource.com/setup_22.x | $SUDO -E bash -
  $SUDO DEBIAN_FRONTEND=noninteractive apt-get install -y nodejs
fi

# ---------------------------------------------------------------- yarn (corepack)
log "Yarn"
if command -v yarn >/dev/null 2>&1; then
  echo "yarn $(yarn -v) already installed"
else
  $SUDO corepack enable
  corepack prepare yarn@1.22.22 --activate
fi

# ---------------------------------------------------------------- MongoDB
log "MongoDB"
if dpkg -s mongodb-org >/dev/null 2>&1; then
  echo "mongodb-org already installed"
else
  # shellcheck source=/dev/null
  CODENAME="$(. /etc/os-release && echo "${VERSION_CODENAME:-jammy}")"
  curl -fsSL https://www.mongodb.org/static/pgp/server-8.0.asc \
    | $SUDO gpg --dearmor --yes -o /usr/share/keyrings/mongodb-server-8.0.gpg
  echo "deb [ arch=amd64,arm64 signed-by=/usr/share/keyrings/mongodb-server-8.0.gpg ] https://repo.mongodb.org/apt/ubuntu ${CODENAME}/mongodb-org/8.0 multiverse" \
    | $SUDO tee /etc/apt/sources.list.d/mongodb-org-8.0.list >/dev/null
  $SUDO apt-get update -y
  $SUDO DEBIAN_FRONTEND=noninteractive apt-get install -y mongodb-org
fi
$SUDO systemctl enable --now mongod

# ---------------------------------------------------------------- Caddy
log "Caddy"
if dpkg -s caddy >/dev/null 2>&1; then
  echo "caddy already installed"
else
  $SUDO DEBIAN_FRONTEND=noninteractive apt-get install -y debian-keyring debian-archive-keyring apt-transport-https
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | $SUDO gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    | $SUDO tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
  $SUDO apt-get update -y
  $SUDO DEBIAN_FRONTEND=noninteractive apt-get install -y caddy
fi

# ---------------------------------------------------------------- .env files
log "Environment files"
ENV_DIRS=(backend frontend)
if [ "$GATEWAY" = "1" ]; then ENV_DIRS+=(gateway); fi
for dir in "${ENV_DIRS[@]}"; do
  if [ -f "$dir/.env.example" ] && [ ! -f "$dir/.env" ]; then
    (umask 077; cp "$dir/.env.example" "$dir/.env")
    $SUDO chown "$RUN_USER" "$dir/.env"
    echo "created $dir/.env from .env.example (private, owned by service user)"
  elif [ -f "$dir/.env" ]; then
    echo "$dir/.env already exists (left unchanged)"
  fi
done
if [ -f backend/.env ] && grep -q '^DB_NAME=your_db_name$' backend/.env; then
  sed -i "s|^DB_NAME=your_db_name$|DB_NAME=${APP_NAME//[^A-Za-z0-9_]/_}|" backend/.env
  echo "backend/.env: DB_NAME placeholder replaced"
fi
if [ -f frontend/.env ]; then
  if grep -q '^VITE_BACKEND_URL=' frontend/.env; then
    sed -i "s|^VITE_BACKEND_URL=.*|VITE_BACKEND_URL=|" frontend/.env
  else
    echo "VITE_BACKEND_URL=" >> frontend/.env
  fi
  echo "frontend/.env: same-origin /api (Caddy proxy)"
fi

# ---------------------------------------------------------------- backend venv
HAS_BACKEND=0
if [ -f backend/requirements.txt ]; then
  log "Backend virtualenv"
  [ -d venv ] || python3 -m venv venv
  ./venv/bin/pip install --upgrade pip
  REQUIREMENTS="backend/requirements.txt"
  if [ "$GATEWAY" = "1" ]; then REQUIREMENTS="backend/requirements-gateway.txt"; fi
  ./venv/bin/pip install -r "$REQUIREMENTS"
  if [ "$GATEWAY" = "1" ]; then ./run-gateway.sh --check >/dev/null; fi
  [ -f backend/server.py ] && HAS_BACKEND=1
else
  echo "backend/requirements.txt not found - skipping backend venv"
fi

# ---------------------------------------------------------------- frontend build
log "Frontend build"
(cd frontend && yarn install --frozen-lockfile && yarn build)
$SUDO mkdir -p "/var/www/${APP_NAME}"
$SUDO rsync -a --delete frontend/dist/ "/var/www/${APP_NAME}/"

# ---------------------------------------------------------------- optional systemd gateway
GATEWAY_UNIT=""
if [ "$GATEWAY" = "1" ]; then
  log "systemd service ${APP_NAME}-gateway"
  $SUDO tee "/etc/systemd/system/${APP_NAME}-gateway.service" >/dev/null <<UNIT
[Unit]
Description=${APP_NAME} arena2api gateway
After=network.target

[Service]
User=${RUN_USER}
WorkingDirectory=${APP_DIR}
# gateway/.env is loaded by the launcher. Default binding is loopback only.
# The extension/session store is in memory: one worker, no reload.
ExecStart=${APP_DIR}/venv/bin/python -m gateway
Environment=PYTHONDONTWRITEBYTECODE=1
NoNewPrivileges=true
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT
  $SUDO systemctl daemon-reload
  $SUDO systemctl enable "${APP_NAME}-gateway"
  $SUDO systemctl restart "${APP_NAME}-gateway"
  GATEWAY_UNIT="${APP_NAME}-gateway.service"
fi

# ---------------------------------------------------------------- systemd backend
if [ "$HAS_BACKEND" = "1" ]; then
  log "systemd service ${APP_NAME}-backend"
  $SUDO tee "/etc/systemd/system/${APP_NAME}-backend.service" >/dev/null <<UNIT
[Unit]
Description=${APP_NAME} backend (uvicorn)
After=network.target mongod.service ${GATEWAY_UNIT}
Wants=mongod.service ${GATEWAY_UNIT}

[Service]
User=${RUN_USER}
WorkingDirectory=${APP_DIR}/backend
# No --reload in production: nothing is watched, so job repos (R2A_DATA_DIR, default
# ${APP_DIR}/data) can never restart the service. Keep a single worker (in-process poller/scheduler).
ExecStart=${APP_DIR}/venv/bin/uvicorn server:app --host 127.0.0.1 --port 8001 --workers 1
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT
  $SUDO systemctl daemon-reload
  $SUDO systemctl enable "${APP_NAME}-backend"
  $SUDO systemctl restart "${APP_NAME}-backend"
else
  echo "backend/server.py not found - skipping systemd backend service"
fi

# ---------------------------------------------------------------- Caddy config
log "Caddyfile"
if [ -f /etc/caddy/Caddyfile ]; then
  $SUDO cp /etc/caddy/Caddyfile "/etc/caddy/Caddyfile.bak.$(date +%Y%m%d%H%M%S)"
fi
$SUDO tee /etc/caddy/Caddyfile >/dev/null <<CADDY
{
	auto_https off
}

:${APP_PORT} {
	root * /var/www/${APP_NAME}
	encode gzip

	handle /api/* {
		reverse_proxy 127.0.0.1:8001
	}

	handle {
		try_files {path} /index.html
		file_server
	}
}
CADDY
$SUDO caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
$SUDO systemctl enable caddy
$SUDO systemctl restart caddy

# ---------------------------------------------------------------- firewall
if command -v ufw >/dev/null 2>&1 && $SUDO ufw status 2>/dev/null | grep -q 'Status: active'; then
  $SUDO ufw allow "${APP_PORT}/tcp"
fi

log "Done"
echo "App URL: ${APP_URL}"
echo "Status:  systemctl status caddy mongod"
echo "Logs:    journalctl -u caddy -f"
if [ "$GATEWAY" = "1" ]; then
  echo "Gateway: systemctl status ${APP_NAME}-gateway"
  echo "         journalctl -u ${APP_NAME}-gateway -f"
  echo "Install the browser extension manually; keep the gateway private (use SSH forwarding remotely)."
fi
if [ "$HAS_BACKEND" = "1" ]; then
  echo "Backend: systemctl status ${APP_NAME}-backend"
  echo "         journalctl -u ${APP_NAME}-backend -f"
fi
