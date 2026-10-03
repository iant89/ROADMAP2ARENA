"""Local OpenAI-compatible provider stand-in for provider tests (NOT a real provider).

The first path segment selects the behaviour, so one process serves many "providers":
    http://127.0.0.1:<port>/<mode>/v1/models  and  /<mode>/v1/chat/completions
Modes:
  ok        - no auth; models [stub-a, stub-b, stub-c]; chat returns a fenced file
  auth      - requires "Authorization: Bearer sk-test-good-key-123456"; else 401
  echo      - 401 whose error message ECHOES the received bearer key and X-Title header (redaction tests)
  forbidden - 403      notfound - 404      ratelimit - 429      down - 500      busy - 503
  badjson   - /models returns 200 with a non-JSON body
  bare      - /models returns a bare JSON list of {"id": ...}
  slow      - sleeps STUB_SLOW_SECONDS (default 20) before answering
  missingmodel - /models ok, chat 404 {"error": {"message": "model not found"}}
GET /_requests returns every received request (method, path, headers) for assertions.

Run: ./venv/bin/python backend/tests/provider_stub.py --port 0  (prints the port)
"""
from __future__ import annotations

import argparse
import asyncio
import os
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

GOOD_KEY = "sk-test-good-key-123456"
SLOW = float(os.environ.get("STUB_SLOW_SECONDS", "20"))
app = FastAPI(title="provider stub")
REQUESTS: list[dict] = []
FENCE = "```"


def _err(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": {"message": message, "type": "stub", "code": status}}, status_code=status)


def _gate(mode: str, request: Request) -> JSONResponse | None:
    auth = request.headers.get("authorization", "")
    if mode == "auth" and auth != f"Bearer {GOOD_KEY}":
        return _err(401, "Incorrect API key provided")
    if mode == "echo":
        return _err(401, f"Invalid key {auth.removeprefix('Bearer ')} with title {request.headers.get('x-title', '')}")
    codes = {"forbidden": (403, "Your account has no access to this model"), "notfound": (404, "Not found"),
             "ratelimit": (429, "Rate limit reached for requests"), "down": (500, "Internal server error"),
             "busy": (503, "Service unavailable")}
    if mode in codes:
        return _err(*codes[mode])
    return None


def _record(request: Request) -> None:
    REQUESTS.append({"method": request.method, "path": request.url.path, "headers": dict(request.headers)})


@app.get("/_requests")
async def requests_log():
    return REQUESTS


@app.post("/_reset")
async def reset():
    REQUESTS.clear()
    return {"ok": True}


@app.get("/{mode}/v1/models")
async def models(mode: str, request: Request):
    _record(request)
    if mode == "slow":
        await asyncio.sleep(SLOW)
    if (r := _gate(mode, request)) is not None:
        return r
    if mode == "badjson":
        return PlainTextResponse("<html>not json</html>")
    data = [{"id": m, "object": "model", "owned_by": "stub"} for m in ("stub-b", "stub-a", "stub-c", "stub-a")]
    if mode == "bare":
        return JSONResponse(data)
    return {"object": "list", "data": data}


@app.post("/{mode}/v1/chat/completions")
async def chat(mode: str, request: Request):
    _record(request)
    body = await request.json()
    if mode == "slow":
        await asyncio.sleep(SLOW)
    if (r := _gate(mode, request)) is not None:
        return r
    if mode == "missingmodel":
        return _err(404, f"The model `{body.get('model')}` does not exist")
    turn = sum(1 for m in body.get("messages", []) if m.get("role") == "user")
    content = (f"(provider stub turn {turn}, model {body.get('model')})\n"
               f"{FENCE}python:app/step{turn}.py\nprint('step {turn}')\n{FENCE}\n")
    return {"id": f"chatcmpl-{uuid.uuid4().hex[:12]}", "object": "chat.completion", "created": int(time.time()),
            "model": body.get("model"), "choices": [{"index": 0, "finish_reason": "stop",
                                                    "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}


if __name__ == "__main__":
    import uvicorn
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
