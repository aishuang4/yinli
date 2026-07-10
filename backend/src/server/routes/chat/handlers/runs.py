"""Chat Run 查询、订阅、重跑和取消端点。"""

import asyncio
import json

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from src.server.routes.agents import get_registered_agent
from src.server.routes.chat.background_runs import (
    cancel_chat_background_run,
    get_chat_background_run,
    list_chat_background_runs,
    serialize_chat_background_run,
    subscribe_chat_background_run,
    subscribe_chat_run_state,
)
from src.server.routes.chat.handlers.send import send_message
from src.server.routes.chat.models import ChatMessage
from src.server.routes.chat.router import router
from src.server.routes.chat.team_runtime.registry import get_team_service
from src.server.routes.teams import get_registered_team
from src.storage.database import get_latest_user_chat_message


class TeamMailRequest(BaseModel):
    """用户向 active TeamRun 成员发送消息。"""

    recipient: str = Field(min_length=1)
    content: str = Field(min_length=1, max_length=20_000)


@router.post("/runs/{run_id}/team/mail")
async def send_team_mail(run_id: str, data: TeamMailRequest) -> dict:
    """通过 Mailbox 干预当前 TeamRun。"""

    service = await get_team_service(run_id)
    if service is None:
        raise HTTPException(status_code=409, detail="TeamRun is not active")
    try:
        message = await service.send_user_mail(
            recipient=data.recipient,
            content=data.content,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"message": message}


@router.get("/runs/{run_id}/stream")
async def stream_run(run_id: str, replay: bool = True) -> StreamingResponse:
    """重新订阅后台 Chat Run 事件。"""

    return StreamingResponse(
        subscribe_chat_background_run(run_id, replay=replay),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/runs")
async def list_runs() -> dict:
    """列出当前进程内后台 Chat Run。"""

    runs = await list_chat_background_runs()
    return {"runs": [serialize_chat_background_run(run) for run in runs]}


@router.get("/runs/{run_id}")
async def get_run(run_id: str) -> dict:
    """读取一个后台 Chat Run 的状态和事件缓存。"""

    run = await get_chat_background_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return {
        **serialize_chat_background_run(run),
        "events": run.events,
    }


@router.get("/runs/{run_id}/state")
async def get_run_state(run_id: str) -> dict:
    """读取一个后台 Chat Run 的前端状态快照。"""

    run = await get_chat_background_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if not run.state:
        from src.server.routes.chat.event_hub import initial_run_state

        return initial_run_state(run)
    from src.server.routes.chat.event_hub import normalize_run_state

    return normalize_run_state(run.state)


@router.get("/runs/{run_id}/state/stream")
async def stream_run_state(run_id: str) -> StreamingResponse:
    """订阅 RunState 中间层状态流。"""

    return StreamingResponse(
        subscribe_chat_run_state(run_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/runs/{run_id}/rerun")
async def rerun_run(run_id: str) -> dict:
    """基于历史 Run 的用户输入重新创建一个后台 Chat Run。"""

    source_run = await get_chat_background_run(run_id)
    if source_run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if source_run.status in {"running", "waiting", "cancelling"}:
        raise HTTPException(status_code=409, detail="Only terminal runs can be rerun")

    response = await send_message(_build_rerun_chat_message(source_run))
    new_run_id = await _read_started_run_id(response)
    new_run = await get_chat_background_run(new_run_id)
    if new_run is None:
        raise HTTPException(status_code=500, detail="Rerun was started but cannot be loaded")
    return serialize_chat_background_run(new_run)


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str) -> dict:
    """取消一个仍在运行的后台 Chat Run。"""

    run = await cancel_chat_background_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return serialize_chat_background_run(run)


def _build_rerun_chat_message(source_run) -> ChatMessage:
    """从历史 Run 还原一条新的 Chat 请求，使用当前 Agent/Team 配置执行。"""

    original_user = get_latest_user_chat_message(source_run.session_id, source_run.task_text)
    message = source_run.task_text or str((original_user or {}).get("content") or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="Run task is empty and cannot be rerun")

    images = list((original_user or {}).get("images") or [])
    if source_run.mode == "team" and source_run.team_id:
        team = get_registered_team(source_run.team_id)
        if not team:
            raise HTTPException(status_code=404, detail="Team for rerun not found")
        leader = get_registered_agent(team.leader_agent_id)
        return ChatMessage(
            session_id=source_run.session_id,
            message=message,
            agent_id=team.leader_agent_id,
            agent_name=leader.name if leader else source_run.agent_name,
            team_id=team.id,
            team_name=team.name,
            team_members=[member.model_dump(by_alias=True) for member in team.members],
            images=images,
        )

    agent = get_registered_agent(source_run.agent_id) if source_run.agent_id else None
    if source_run.agent_id and not agent:
        raise HTTPException(status_code=404, detail="Agent for rerun not found")
    return ChatMessage(
        session_id=source_run.session_id,
        message=message,
        agent_id=source_run.agent_id or None,
        agent_name=agent.name if agent else source_run.agent_name,
        images=images,
    )


async def _read_started_run_id(response: StreamingResponse) -> str:
    """读取 send_message 返回流的首个 RUN 事件，拿到新建后台 Run ID。"""

    iterator = response.body_iterator
    try:
        async with asyncio.timeout(5):
            async for chunk in iterator:
                raw = chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk)
                event_type, data = _parse_sse_chunk(raw)
                if event_type == "RUN":
                    run_id = str(data.get("run_id") or data.get("runId") or "")
                    if run_id:
                        return run_id
                if event_type == "ERROR":
                    raise HTTPException(status_code=500, detail=str(data.get("message") or "Rerun failed"))
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail="Timed out waiting for rerun to start") from exc
    finally:
        close = getattr(iterator, "aclose", None)
        if callable(close):
            await close()
    raise HTTPException(status_code=500, detail="Rerun did not emit a RUN event")


def _parse_sse_chunk(raw: str) -> tuple[str, dict]:
    """解析单个 SSE 文本块。"""

    event_type = ""
    data_lines: list[str] = []
    for line in raw.splitlines():
        if line.startswith("event:"):
            event_type = line.removeprefix("event:").strip()
        elif line.startswith("data:"):
            data_lines.append(line.removeprefix("data:").strip())
    if not data_lines:
        return event_type, {}
    try:
        return event_type, json.loads("\n".join(data_lines))
    except json.JSONDecodeError:
        return event_type, {}
