"""群聊工作室独立路由。

flag 关闭时全部 404 `group_studio_disabled`。
from-chat / 任务卡 / 成员 / 黑板 / 文件 / 审批 / 插话 / 熔断 / 结项已实现。
"""

import asyncio

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import StreamingResponse

from src.group_studio.errors import (
    GROUP_STUDIO_DISABLED,
    GROUP_STUDIO_NOT_IMPLEMENTED,
    GroupStudioError,
    group_studio_http_error,
)
from src.group_studio.models import (
    GroupApproval,
    GroupApprovalDecisionRequest,
    GroupArtifact,
    GroupCloseRequest,
    GroupConfirmRestatementRequest,
    GroupInterruptRequest,
    GroupMemberCreateRequest,
    GroupMemberMuteRequest,
    GroupMemberRemoveRequest,
    GroupMessage,
    GroupMessageCreateRequest,
    GroupPauseRequest,
    GroupResumeRequest,
    GroupStartPhaseRequest,
    GroupStudio,
    GroupStudioFromChatRequest,
    GroupTaskCardUpdateRequest,
    Membership,
    TaskCard,
)
from src.group_studio.service import (
    add_member,
    confirm_restatement as confirm_studio_restatement,
    decide_studio_approval,
    from_chat,
    get_studio,
    get_studio_by_chat_session,
    interrupt_run,
    list_studio_files,
    list_studio_messages,
    mute_member,
    pause_studio,
    post_human_message,
    prepare_start_execution,
    prepare_start_review,
    remove_member,
    resume_studio,
    close_studio as close_group_studio,
    update_task_card,
    upload_studio_file,
)
from src.group_studio.settings import is_group_studio_enabled
from src.group_studio.stream import format_sse, heartbeat_comment, subscribe
from src.storage.database import get_group_studio_snapshot, list_group_studio_events_after

router = APIRouter()


def require_group_studio_enabled() -> None:
    """写/读工作室 API 在 flag 关闭时一律 404。Settings 不走本路由。"""

    if not is_group_studio_enabled():
        raise group_studio_http_error(404, GROUP_STUDIO_DISABLED)


def _not_implemented() -> None:
    raise group_studio_http_error(501, GROUP_STUDIO_NOT_IMPLEMENTED)


EnabledDep = Depends(require_group_studio_enabled)


@router.post("/from-chat", dependencies=[EnabledDep])
async def create_from_chat(body: GroupStudioFromChatRequest) -> GroupStudio:
    """从 Chat 会话升级或回到工作室。"""

    try:
        return from_chat(body)
    except GroupStudioError as exc:
        raise group_studio_http_error(exc.status_code, exc.code) from exc


@router.get("/by-chat/{session_id}", dependencies=[EnabledDep])
async def get_by_chat_session(session_id: str) -> GroupStudio | None:
    """按 Chat session 探活未归档工作室。"""

    return get_studio_by_chat_session(session_id)


@router.get("/{group_id}", dependencies=[EnabledDep])
async def get_studio_snapshot(group_id: str) -> GroupStudio:
    """读取工作室快照（含最近 100 条 messages）。"""

    try:
        return get_studio(group_id)
    except GroupStudioError as exc:
        raise group_studio_http_error(exc.status_code, exc.code) from exc


@router.get("/{group_id}/messages", dependencies=[EnabledDep])
async def list_messages(group_id: str, before: str | None = None, limit: int = 100) -> dict:
    """分页读取更早的群消息。"""

    try:
        return {"messages": list_studio_messages(group_id, before=before, limit=limit)}
    except GroupStudioError as exc:
        raise group_studio_http_error(exc.status_code, exc.code) from exc


@router.get("/{group_id}/events", dependencies=[EnabledDep])
async def stream_events(group_id: str, afterSeq: int | None = None):
    """独立 SSE。契约见 docs/group-studio-event-contract.md。"""

    snapshot = get_group_studio_snapshot(group_id)
    if snapshot is None:
        raise group_studio_http_error(404, "not_found")

    async def event_stream():
        state = dict(snapshot)
        yield format_sse({
            "seq": None,
            "groupId": group_id,
            "runId": snapshot.get("currentRunId"),
            "type": "GROUP_STUDIO_STATE",
            "status": snapshot.get("status") or "",
            "payload": state,
            "createdAt": snapshot.get("updatedAt") or snapshot.get("createdAt"),
        })
        after = int(afterSeq or 0)
        for event in list_group_studio_events_after(group_id, after):
            yield format_sse(event)
        async for live in subscribe(group_id):
            seq = live.get("seq")
            if seq is not None and int(seq) <= after:
                continue
            yield format_sse(live)

    return StreamingResponse(
        _heartbeat_wrapper(event_stream()),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _heartbeat_wrapper(source):
    """在订阅空闲时输出 : heartbeat。"""

    queue: asyncio.Queue = asyncio.Queue()

    async def pump():
        async for chunk in source:
            await queue.put(chunk)
        await queue.put(None)

    task = asyncio.create_task(pump())
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=15)
            except asyncio.TimeoutError:
                yield heartbeat_comment()
                continue
            if item is None:
                break
            yield item
    finally:
        task.cancel()



@router.post("/{group_id}/pause", dependencies=[EnabledDep])
async def pause_studio_route(group_id: str, body: GroupPauseRequest) -> GroupStudio:
    try:
        return pause_studio(group_id, project_id=body.project_id)
    except GroupStudioError as exc:
        raise group_studio_http_error(exc.status_code, exc.code) from exc


@router.post("/{group_id}/resume", dependencies=[EnabledDep])
async def resume_studio_route(group_id: str, body: GroupResumeRequest) -> GroupStudio:
    try:
        return resume_studio(group_id, project_id=body.project_id, max_turns=body.max_turns)
    except GroupStudioError as exc:
        raise group_studio_http_error(exc.status_code, exc.code) from exc


@router.post("/{group_id}/messages", dependencies=[EnabledDep])
async def post_message(group_id: str, body: GroupMessageCreateRequest) -> GroupMessage:
    try:
        return post_human_message(
            group_id,
            project_id=body.project_id,
            body=body.body,
            mentions=body.mentions,
        )
    except GroupStudioError as exc:
        raise group_studio_http_error(exc.status_code, exc.code) from exc


@router.post("/{group_id}/interrupt", dependencies=[EnabledDep])
async def interrupt_studio(group_id: str, body: GroupInterruptRequest) -> dict:
    try:
        interrupt_run(group_id, project_id=body.project_id)
    except GroupStudioError as exc:
        raise group_studio_http_error(exc.status_code, exc.code) from exc
    return {"ok": True}


@router.put("/{group_id}/task-card", dependencies=[EnabledDep])
async def update_task_card_route(group_id: str, body: GroupTaskCardUpdateRequest) -> TaskCard:
    try:
        return update_task_card(group_id, body)
    except GroupStudioError as exc:
        raise group_studio_http_error(exc.status_code, exc.code) from exc


@router.post("/{group_id}/members", dependencies=[EnabledDep])
async def add_member_route(group_id: str, body: GroupMemberCreateRequest) -> Membership:
    try:
        return add_member(group_id, body)
    except GroupStudioError as exc:
        raise group_studio_http_error(exc.status_code, exc.code) from exc


@router.post("/{group_id}/members/{member_id}/mute", dependencies=[EnabledDep])
async def mute_member_route(
    group_id: str,
    member_id: str,
    body: GroupMemberMuteRequest,
) -> Membership:
    try:
        return mute_member(group_id, member_id, body.muted, project_id=body.project_id)
    except GroupStudioError as exc:
        raise group_studio_http_error(exc.status_code, exc.code) from exc


@router.post("/{group_id}/members/{member_id}/remove", dependencies=[EnabledDep])
async def remove_member_route(
    group_id: str,
    member_id: str,
    body: GroupMemberRemoveRequest,
) -> Membership:
    try:
        return remove_member(group_id, member_id, project_id=body.project_id)
    except GroupStudioError as exc:
        raise group_studio_http_error(exc.status_code, exc.code) from exc


@router.post("/{group_id}/close", dependencies=[EnabledDep])
async def close_studio_route(group_id: str, body: GroupCloseRequest) -> GroupStudio:
    try:
        return close_group_studio(group_id, project_id=body.project_id, confirm=body.confirm)
    except GroupStudioError as exc:
        raise group_studio_http_error(exc.status_code, exc.code) from exc


@router.post("/{group_id}/confirm-restatement", dependencies=[EnabledDep])
async def confirm_restatement_route(group_id: str, body: GroupConfirmRestatementRequest) -> GroupStudio:
    try:
        return confirm_studio_restatement(group_id, project_id=body.project_id)
    except GroupStudioError as exc:
        raise group_studio_http_error(exc.status_code, exc.code) from exc


@router.post("/{group_id}/approvals", dependencies=[EnabledDep])
async def decide_approval(group_id: str, body: GroupApprovalDecisionRequest) -> GroupApproval:
    try:
        return decide_studio_approval(
            group_id,
            project_id=body.project_id,
            approval_id=body.approval_id,
            decision=body.decision,
        )
    except GroupStudioError as exc:
        raise group_studio_http_error(exc.status_code, exc.code) from exc


@router.post("/{group_id}/start-execution", dependencies=[EnabledDep])
async def start_execution(group_id: str, body: GroupStartPhaseRequest) -> GroupStudio:
    try:
        return prepare_start_execution(group_id, body.project_id)
    except GroupStudioError as exc:
        raise group_studio_http_error(exc.status_code, exc.code) from exc


@router.post("/{group_id}/start-review", dependencies=[EnabledDep])
async def start_review(group_id: str, body: GroupStartPhaseRequest) -> GroupStudio:
    try:
        return prepare_start_review(group_id, body.project_id)
    except GroupStudioError as exc:
        raise group_studio_http_error(exc.status_code, exc.code) from exc


@router.post("/{group_id}/files", dependencies=[EnabledDep])
async def upload_file(
    group_id: str,
    file: UploadFile = File(...),
    project_id: str = Query("", alias="projectId"),
) -> GroupArtifact:
    try:
        return await upload_studio_file(group_id, file, project_id=project_id)
    except GroupStudioError as exc:
        raise group_studio_http_error(exc.status_code, exc.code) from exc


@router.get("/{group_id}/files", dependencies=[EnabledDep])
async def list_files(group_id: str) -> dict:
    try:
        return {"files": list_studio_files(group_id)}
    except GroupStudioError as exc:
        raise group_studio_http_error(exc.status_code, exc.code) from exc
