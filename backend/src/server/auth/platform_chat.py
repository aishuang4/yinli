"""桌面端把 platform: 模型转到托管网关，并把回复写回本机会话。"""

from __future__ import annotations

import json

import httpx
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from src.server.auth.desktop_account import load_desktop_account
from src.server.routes.chat.models import ChatMessage
from src.server.routes.chat.store import _append_assistant_message, _append_user_message
from src.storage.database import DEFAULT_PROJECT_ID


async def fetch_platform_reply(*, base_url: str, token: str, model_config_id: str, text: str) -> str:
    """向托管网关取回这一轮文本。测试可替换此函数。"""

    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            f"{base_url}/api/models/complete",
            headers={"Authorization": f"Bearer {token}"},
            json={"modelConfigId": model_config_id, "text": text},
        )
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="平台模型调用失败")
    reply = ""
    for line in response.text.splitlines():
        if not line.startswith("data:"):
            continue
        payload = json.loads(line[5:].strip() or "{}")
        reply += str(payload.get("text") or "")
    return reply


async def platform_chat_response(data: ChatMessage) -> StreamingResponse:
    account = load_desktop_account()
    if account is None:
        raise HTTPException(status_code=401, detail="登录后才能使用平台模型")
    session_id = data.session_id or "platform-session"
    project_id = data.project_id or DEFAULT_PROJECT_ID
    reply = await fetch_platform_reply(
        base_url=account["baseUrl"],
        token=account["token"],
        model_config_id=str(data.model_config_id or ""),
        text=data.message,
    )
    _append_user_message(
        session_id=session_id,
        agent_id=data.agent_id or "default",
        agent_name=data.agent_name or "助手",
        mode=data.mode,
        model_name=str(data.model_config_id or ""),
        content=data.message,
        images=list(data.images),
        files=list(data.files),
        project_id=project_id,
    )
    _append_assistant_message(
        session_id=session_id,
        agent_id=data.agent_id or "default",
        agent_name=data.agent_name or "助手",
        model_name=str(data.model_config_id or ""),
        content=reply,
        error=None,
    )

    async def body():
        yield f"data: {json.dumps({'text': reply}, ensure_ascii=False)}\n\n"

    return StreamingResponse(body(), media_type="text/event-stream")
