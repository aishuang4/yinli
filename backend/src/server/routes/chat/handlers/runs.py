"""Chat Run 查询、订阅、重跑和取消端点。"""

from fastapi import HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from src.server.routes.agents import get_registered_agent
from src.server.routes.chat.background_runs import (
    cancel_chat_background_run,
    get_chat_background_run,
    get_chat_projection_snapshot,
    list_chat_background_runs,
    serialize_chat_background_run,
    subscribe_chat_background_run,
)
from src.server.routes.chat.handlers.send import start_chat_run
from src.server.routes.chat.models import ChatMessage
from src.server.routes.chat.lifecycle import ACTIVE_RUN_STATUSES
from src.server.routes.chat.router import router
from src.server.routes.chat.team_runtime.registry import get_team_service
from src.storage.database import get_latest_user_chat_message
from src.storage.database import DEFAULT_PROJECT_ID


class TeamMailRequest(BaseModel):
    """用户向 active TeamRun 成员发送消息。"""

    recipient: str = Field(min_length=1)
    content: str = Field(min_length=1, max_length=20_000)
    dispatch_mode: str = Field(default="context", alias="dispatchMode")
    task_id: str = Field(default="", alias="taskId")


async def _get_project_run(run_id: str, project_id: str):
    """读取当前 Project 内的 Run，避免仅凭 runId 跨 Project 操作。"""

    run = await get_chat_background_run(run_id)
    if run is None or run.project_id != project_id:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@router.post("/runs/{run_id}/team/mail")
async def send_team_mail(
    run_id: str,
    data: TeamMailRequest,
    project_id: str = Query(default=DEFAULT_PROJECT_ID, alias="projectId"),
) -> dict:
    """通过 Mailbox 干预当前 TeamRun。"""

    await _get_project_run(run_id, project_id)
    service = await get_team_service(run_id)
    if service is None:
        raise HTTPException(status_code=409, detail="TeamRun is not active")
    try:
        message = await service.send_user_mail(
            recipient=data.recipient,
            content=data.content,
            dispatch_mode=data.dispatch_mode,
            task_id=data.task_id,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"message": message}


@router.get("/runs/{run_id}/stream")
async def stream_run(
    run_id: str,
    after_seq: int | None = Query(default=None, alias="afterSeq", ge=0),
    project_id: str = Query(default=DEFAULT_PROJECT_ID, alias="projectId"),
) -> StreamingResponse:
    """重新订阅后台 Chat Run 事件。"""

    await _get_project_run(run_id, project_id)
    return StreamingResponse(
        subscribe_chat_background_run(run_id, after_seq=after_seq),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/runs")
async def list_runs(
    project_id: str = Query(default=DEFAULT_PROJECT_ID, alias="projectId"),
) -> dict:
    """列出当前进程内后台 Chat Run。"""

    runs = await list_chat_background_runs(project_id=project_id)
    return {"runs": [serialize_chat_background_run(run) for run in runs]}


@router.get("/runs/{run_id}")
async def get_run(
    run_id: str,
    project_id: str = Query(default=DEFAULT_PROJECT_ID, alias="projectId"),
) -> dict:
    """读取一个后台 Chat Run 的状态和事件缓存。"""

    run = await _get_project_run(run_id, project_id)
    return {
        **serialize_chat_background_run(run),
        "snapshot": get_chat_projection_snapshot(run.id),
    }


@router.get("/runs/{run_id}/state")
async def get_run_state(
    run_id: str,
    project_id: str = Query(default=DEFAULT_PROJECT_ID, alias="projectId"),
) -> dict:
    """读取一个后台 Chat Run 的前端状态快照。"""

    await _get_project_run(run_id, project_id)
    return get_chat_projection_snapshot(run_id)


@router.post("/runs/{run_id}/rerun")
async def rerun_run(
    run_id: str,
    project_id: str = Query(default=DEFAULT_PROJECT_ID, alias="projectId"),
) -> dict:
    """基于历史 Run 的用户输入重新创建一个后台 Chat Run。"""

    source_run = await _get_project_run(run_id, project_id)
    if source_run.status in ACTIVE_RUN_STATUSES:
        raise HTTPException(status_code=409, detail="Only terminal runs can be rerun")
    if getattr(source_run, "trigger_source", "user") == "scheduled_job":
        raise HTTPException(
            status_code=409,
            detail="Scheduled job runs cannot be rerun as interactive chat. Use Job run-now instead.",
        )

    new_run = await start_chat_run(_build_rerun_chat_message(source_run))
    return serialize_chat_background_run(new_run)


@router.post("/runs/{run_id}/cancel")
async def cancel_run(
    run_id: str,
    project_id: str = Query(default=DEFAULT_PROJECT_ID, alias="projectId"),
) -> dict:
    """取消一个仍在运行的后台 Chat Run。"""

    await _get_project_run(run_id, project_id)
    run = await cancel_chat_background_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return serialize_chat_background_run(run)


def _build_rerun_chat_message(source_run) -> ChatMessage:
    """从历史 Run 还原一条新的 Chat 请求，使用当前 Leader Agent 配置执行。"""

    original_user = get_latest_user_chat_message(source_run.session_id, source_run.task_text)
    message = source_run.task_text or str((original_user or {}).get("content") or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="Run task is empty and cannot be rerun")

    images = list((original_user or {}).get("images") or [])
    agent = get_registered_agent(source_run.agent_id) if source_run.agent_id else None
    if source_run.agent_id and not agent:
        raise HTTPException(status_code=404, detail="Agent for rerun not found")
    return ChatMessage(
        session_id=source_run.session_id,
        project_id=getattr(source_run, "project_id", DEFAULT_PROJECT_ID),
        message=message,
        agent_id=source_run.agent_id or None,
        agent_name=agent.name if agent else source_run.agent_name,
        mode=source_run.mode,
        images=images,
    )
