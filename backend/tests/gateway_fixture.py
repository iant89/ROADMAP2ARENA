"""TEST ONLY: real pinned arena2api with all outbound Arena HTTP simulated.

Never used by run.sh/run-gateway.sh. CLI requires R2A_TEST_GATEWAY=1 and binds
loopback only. Control routes require the same throwaway key as chat/models.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import httpx
from fastapi import HTTPException, Request

from gateway.runtime import GatewayConfig, check_upstream, load_upstream

MODEL = "Pipeline Text"


def create_fixture(key: str):
    if not key:
        raise ValueError("The test gateway requires a throwaway API key")
    check_upstream()
    upstream = load_upstream(GatewayConfig(api_key=key, log_level="warning"))
    app = upstream.app
    state = {"requests": [], "fail_turn": None, "delay": 0.0}

    def seed():
        upstream.store = upstream.Store()
        upstream.store.push({
            "auth_token": "synthetic-session-never-used-on-network",
            "cookies": {"arena-auth-prod-v1": "synthetic-cookie"},
            "v3_tokens": [{"token": "synthetic-token-" + str(i) + "x" * 30, "age_ms": 0} for i in range(10)],
            "models": [{"publicName": MODEL, "id": "pipeline-text-id",
                        "capabilities": {"inputCapabilities": ["text"], "outputCapabilities": ["text"]}}],
        })

    seed()

    async def respond(request):
        payload = json.loads(request.content)
        prompt = payload["userMessage"]["content"]
        turn = prompt.count("<|user|>") or 1
        state["requests"].append({"prompt": prompt, "turn": turn, "model_id": payload["modelAId"]})
        if state["delay"]:
            await asyncio.sleep(state["delay"])
        if state["fail_turn"] == turn:
            state["fail_turn"] = None  # one failure; resume can retry the same step
            return httpx.Response(429, text="simulated rate limit")
        content = f"```python:app/main.py\nprint('pipeline turn {turn}')\n```\n"
        content += f"```markdown:README.md\nCompleted synthetic turn {turn}.\n```"
        stream = "a0:" + json.dumps(content) + "\nad:" + json.dumps({"finishReason": "stop"}) + "\n"
        return httpx.Response(200, text=stream)

    # Only upstream's namespace is changed; SDK and app use real localhost HTTP.
    upstream.httpx = SimpleNamespace(AsyncClient=lambda **kw: httpx.AsyncClient(
        transport=httpx.MockTransport(respond), trust_env=False, **kw))

    @app.post("/_test/reset")
    async def reset(request: Request):
        upstream.verify_api_key(request)
        body = await request.json()
        delay, fail = body.get("delay", 0), body.get("fail_turn")
        if isinstance(delay, bool) or not isinstance(delay, (int, float)) or not 0 <= delay <= 5:
            raise HTTPException(400, "Invalid test delay")
        if fail is not None and (isinstance(fail, bool) or not isinstance(fail, int) or not 1 <= fail <= 10):
            raise HTTPException(400, "Invalid test failure turn")
        state.update(requests=[], fail_turn=fail, delay=float(delay))
        seed()
        if body.get("active", True) is False:
            upstream.store.last_push = 0
        return {"ok": True}

    @app.get("/_test/requests")
    async def requests(request: Request):
        upstream.verify_api_key(request)
        return {"requests": state["requests"]}

    return app


def main():
    if os.environ.get("R2A_TEST_GATEWAY") != "1":
        raise RuntimeError("Test-only gateway: R2A_TEST_GATEWAY=1 is required")
    key = os.environ.get("TEST_GATEWAY_KEY", "")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    cfg = GatewayConfig(port=args.port, api_key=key)
    import uvicorn
    uvicorn.run(create_fixture(cfg.api_key), host="127.0.0.1", port=cfg.port, workers=1, log_level="warning")


if __name__ == "__main__":
    main()
