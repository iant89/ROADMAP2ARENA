"""Run with ./run-gateway.sh or ./venv/bin/python -m gateway."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from . import UPSTREAM_URL
from .runtime import GatewayConfig, check_upstream, load_upstream


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Start the pinned arena2api gateway (no MongoDB required).")
    parser.add_argument("--host", help="Bind address; default 127.0.0.1, or GATEWAY_HOST")
    parser.add_argument("--port", type=int, help="Listen port; default 9090, or GATEWAY_PORT")
    parser.add_argument("--env-file", type=Path, help="Optional gateway configuration file; default gateway/.env")
    parser.add_argument("--check", action="store_true", help="Validate configuration/dependencies without starting a server")
    args = parser.parse_args(argv)
    try:
        cfg = GatewayConfig.load(args.env_file, host=args.host, port=args.port)
        revision = check_upstream()
        upstream = load_upstream(cfg)
        import uvicorn
        if args.check:
            print(json.dumps({"service": "arena2api", "upstream": UPSTREAM_URL, "revision": revision,
                              "host": cfg.host, "port": cfg.port, "api_key_configured": bool(cfg.api_key)}, indent=2))
            return 0
        url_host = f"[{cfg.host}]" if ":" in cfg.host else cfg.host
        print(f"arena2api: http://{url_host}:{cfg.port} (one worker, no reload)", flush=True)
        print("Waiting for the browser extension; HTTP health alone does not mean Arena is connected.", flush=True)
        uvicorn.run(upstream.app, host=cfg.host, port=cfg.port, workers=1, log_level=cfg.log_level)
        return 0
    except (ValueError, RuntimeError, ImportError, OSError) as exc:
        print(f"Gateway startup failed: {exc}", file=sys.stderr)
        print("Install dependencies: ./venv/bin/python -m pip install -r backend/requirements-gateway.txt", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
