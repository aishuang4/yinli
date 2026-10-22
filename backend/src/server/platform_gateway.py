"""云端 OpenAI 兼容转发。用该用户的平台 Key 调用 key2api，不记录正文。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from src.server.auth.key2api import PLATFORM_PREFIX, Key2ApiError, _active_key, base_url, list_platform_models
from src.server.auth.routes import require_user
from src.storage.control.control_records import record_platform_call

router = APIRouter(prefix="/api/platform/v1", tags=["平台转发"])


def _audit(*, user_id: str, model: str, client_kind: str, prompt: int, completion: int, status: int) -> None:
    record_platform_call(
        user_id=user_id,
        model_config_id=model,
        client_kind=client_kind,
        prompt_tokens=prompt,
        completion_tokens=completion,
        status=status,
    )


def _upstream_message(raw: bytes) -> str:
    try:
        payload = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError):
        return ""
    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        return ""
    return str(error.get("message") or "")[:500]


def _map_status(code: int, raw: bytes) -> tuple[int, str]:
    if code in {401, 403}:
        return 502, "平台 Key 不可用"
    if code == 429:
        return 429, _upstream_message(raw) or "平台额度不足"
    if 400 <= code < 500:
        return code, _upstream_message(raw) or "平台请求被拒绝"
    return 502, "平台暂时无法连接"


def _usage(payload: dict) -> tuple[int, int] | None:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None
    return int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)


@router.get("/models")
async def models(request: Request) -> dict:
    found = require_user(request)
    try:
        listed = list_platform_models(found["user"]["id"])
    except Key2ApiError as exc:
        raise HTTPException(status_code=502, detail="平台 Key 不可用") from exc
    from src.models.platform_layer import platform_capabilities

    data = []
    for item in listed:
        model_id = str(item.get("id") or "")
        name = model_id.removeprefix(PLATFORM_PREFIX)
        data.append({
            "id": model_id,
            "object": "model",
            "owned_by": "yinli",
            "capabilities": platform_capabilities(name),
        })
    return {"object": "list", "data": data}


@router.post("/chat/completions")
async def chat_completions(request: Request):
    found = require_user(request)
    user_id = str(found["user"]["id"])
    client_kind = str(found["session"].get("clientKind") or "")
    try:
        body = await request.json()
    except (UnicodeError, json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=422, detail="请求不是 JSON") from None
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="请求不是 JSON")
    model = str(body.get("model") or "")
    name = model.removeprefix(PLATFORM_PREFIX) if model.startswith(PLATFORM_PREFIX) else ""
    try:
        record = _active_key(user_id)
        allowed = {
            str(item.get("id") or "").removeprefix(PLATFORM_PREFIX)
            for item in list_platform_models(user_id)
        }
    except Key2ApiError:
        _audit(user_id=user_id, model=model, client_kind=client_kind, prompt=0, completion=0, status=502)
        raise HTTPException(status_code=502, detail="平台 Key 不可用") from None
    if not name or name not in allowed:
        _audit(user_id=user_id, model=model, client_kind=client_kind, prompt=0, completion=0, status=404)
        raise HTTPException(status_code=404, detail="模型不存在")
    payload = dict(body)
    payload["model"] = name
    if payload.get("stream") is True:
        options = payload.get("stream_options")
        payload["stream_options"] = {**(options if isinstance(options, dict) else {}), "include_usage": True}
        return await _stream(
            user_id=user_id,
            model=model,
            client_kind=client_kind,
            token=str(record["token"]),
            payload=payload,
        )
    return await _complete(
        user_id=user_id,
        model=model,
        client_kind=client_kind,
        token=str(record["token"]),
        payload=payload,
    )


async def _complete(*, user_id: str, model: str, client_kind: str, token: str, payload: dict):
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=10.0)) as client:
            response = await client.post(
                f"{base_url()}/v1/chat/completions",
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
            )
    except httpx.HTTPError:
        _audit(user_id=user_id, model=model, client_kind=client_kind, prompt=0, completion=0, status=502)
        raise HTTPException(status_code=502, detail="平台暂时无法连接") from None
    raw = response.content
    if response.status_code >= 400:
        status, detail = _map_status(response.status_code, raw)
        _audit(user_id=user_id, model=model, client_kind=client_kind, prompt=0, completion=0, status=status)
        raise HTTPException(status_code=status, detail=detail)
    try:
        parsed = response.json()
    except json.JSONDecodeError:
        _audit(user_id=user_id, model=model, client_kind=client_kind, prompt=0, completion=0, status=502)
        raise HTTPException(status_code=502, detail="平台暂时无法连接") from None
    usage = _usage(parsed) or (0, 0)
    _audit(
        user_id=user_id,
        model=model,
        client_kind=client_kind,
        prompt=usage[0],
        completion=usage[1],
        status=200,
    )
    return JSONResponse(parsed)


async def _stream(*, user_id: str, model: str, client_kind: str, token: str, payload: dict):
    client = httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=10.0))
    try:
        request = client.build_request(
            "POST",
            f"{base_url()}/v1/chat/completions",
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
        )
        response = await client.send(request, stream=True)
    except httpx.HTTPError:
        await client.aclose()
        _audit(user_id=user_id, model=model, client_kind=client_kind, prompt=0, completion=0, status=502)
        raise HTTPException(status_code=502, detail="平台暂时无法连接") from None
    if response.status_code >= 400:
        raw = await response.aread()
        await response.aclose()
        await client.aclose()
        status, detail = _map_status(response.status_code, raw)
        _audit(user_id=user_id, model=model, client_kind=client_kind, prompt=0, completion=0, status=status)
        raise HTTPException(status_code=status, detail=detail)

    async def chunks() -> AsyncIterator[bytes]:
        pending = ""
        usage = (0, 0)
        try:
            async for chunk in response.aiter_bytes():
                pending, found = _feed_usage(pending, chunk)
                if found is not None:
                    usage = found
                yield chunk
        finally:
            await response.aclose()
            await client.aclose()
            _audit(
                user_id=user_id,
                model=model,
                client_kind=client_kind,
                prompt=usage[0],
                completion=usage[1],
                status=200,
            )

    return StreamingResponse(chunks(), media_type="text/event-stream")


def _feed_usage(pending: str, chunk: bytes) -> tuple[str, tuple[int, int] | None]:
    pending += chunk.decode("utf-8", "replace")
    found = None
    while "\n" in pending:
        line, pending = pending.split("\n", 1)
        raw = line.strip()
        if not raw.startswith("data:"):
            continue
        data = raw[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            continue
        parsed = _usage(payload) if isinstance(payload, dict) else None
        if parsed is not None:
            found = parsed
    return pending, found
