"""Routes for OpenAI-compatible providers (/api/providers). See contracts.md "Providers"."""
from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException, Response

import providers
from database import db
from orchestrator import now_iso
from secret_key import key_state

router = APIRouter(prefix="/api/providers")


def _raise(e: providers.ProviderError):
    raise HTTPException(status_code=e.status, detail=e.detail)


async def _public(doc: dict) -> dict:
    return providers.public(doc, await providers.default_id(db))


@router.get("")
async def list_providers():
    default = await providers.default_id(db)
    return {"providers": [providers.public(d, default) for d in await providers.list_docs(db)],
            "default_provider_id": default, "presets": providers.PRESETS, "encryption": key_state()}


@router.post("", status_code=201)
async def create_provider(body: dict = Body(...)):
    try:
        return await _public(await providers.create(db, body, now_iso()))
    except providers.ProviderError as e:
        _raise(e)


@router.post("/test")
async def test_provider(body: dict = Body(...)):
    """Test an unsaved draft (base_url, api_key?, headers?, provider_id?); always 200 with ok true/false."""
    unknown = sorted(set(body) - {"base_url", "api_key", "headers", "provider_id", "preset", "name"})
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown field(s): {', '.join(unknown)}")
    try:
        base = providers.normalize_base_url(body.get("base_url"))
        headers = providers.validate_headers(body.get("headers"))
        stored = await providers.get_doc(db, body["provider_id"]) if body.get("provider_id") else None
        if body.get("provider_id") and not stored:
            raise providers.ProviderError(404, f"Provider {body['provider_id']} not found")
        if body.get("api_key") not in (None, ""):
            key = providers._validate_key(body["api_key"])
        elif stored and stored.get("api_key_enc") and providers.origin(base) != providers.origin(stored["base_url"]):
            raise providers.ProviderError(422, "Enter the API key to test a different host - "
                                               "a stored key is never sent to a different host")
        elif stored:
            key, err = providers.decrypt_key(stored)
            if err:
                raise providers.ProviderError(409, f"Provider '{stored['name']}': {err}")
        else:
            key = None
        # No key of its own: the server ARENA2API_API_KEY, only for exactly the configured gateway URL.
        key = providers.effective_key(key, base)
    except providers.ProviderError as e:
        _raise(e)
    name = (body.get("name") or (stored or {}).get("name") or "provider").strip()[:providers.NAME_MAX] or "provider"
    preset = body.get("preset") or (stored or {}).get("preset")
    return await providers.test_connection(base, key, headers, name=name, arena=preset == "arena2api")


@router.put("/{provider_id}")
async def update_provider(provider_id: str, body: dict = Body(...)):
    try:
        return await _public(await providers.update(db, provider_id, body, now_iso()))
    except providers.ProviderError as e:
        _raise(e)


@router.delete("/{provider_id}", status_code=204)
async def delete_provider(provider_id: str):
    try:
        await providers.delete(db, provider_id)
    except providers.ProviderError as e:
        _raise(e)
    return Response(status_code=204)


@router.post("/{provider_id}/default")
async def make_default(provider_id: str):
    doc = await providers.get_doc(db, provider_id)
    if not doc:
        raise HTTPException(status_code=404, detail=f"Provider {provider_id} not found")
    await providers.set_default(db, provider_id)
    return await _public(doc)


@router.get("/{provider_id}/models")
async def list_models(provider_id: str):
    doc = await providers.get_doc(db, provider_id)
    if not doc:
        raise HTTPException(status_code=404, detail=f"Provider {provider_id} not found")
    key, err = providers.decrypt_key(doc)
    if err:
        raise HTTPException(status_code=409, detail=f"Provider '{doc['name']}': {err}")
    try:
        models = await providers.fetch_models(doc["base_url"], providers.effective_key(key, doc["base_url"]),
                                              doc.get("headers"), name=doc["name"],
                                              arena=doc.get("preset") == "arena2api")
    except providers.ProviderError as e:
        _raise(e)
    return {"models": models, "count": len(models)}
