"""群聊工作室独立路由。

flag 关闭时全部 404 `group_studio_disabled`。
V2：进群即 open；旧 intake / 阶段推进接口 410。
"""

import asyncio

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from src.group_studio.errors import (
    GROUP_STUDIO_DISABLED,
    ILLEGAL_TRANSITION,
    GroupStudioError,
    group_studio_http_error,
)
from src.group_studio.models import (
    GroupApproval,
    GroupApprovalDecisionRequest,
    GroupArtifact,
    GroupCloseRequest,
    GroupConfirmIntakeRequest,
    GroupConfirmRestatementRequest,
    GroupInterruptRequest,
    GroupMemberCreateRequest,
    GroupMemberMuteRequest,
    GroupMemberRemoveRequest,
    GroupMessage,
    GroupMessageCreateRequest,
    GroupPauseRequest,
    GroupRerunRequest,
    GroupResumeRequest,
    GroupStartPhaseRequest,
    GroupStudio,
    GroupStudioFromChatRequest,
    GroupStudioModelUpdateRequest,
    GroupTaskCardUpdateRequest,
    Membership,
    TaskCard,
)
from src.group_studio.service import (
    add_member,
    decide_studio_approval,
    from_chat,
    get_studio,
    get_studio_by_chat_session,
    interrupt_run,
    list_studio_files,
    list_invitable_agents,
    update_studio_model,
    resolve_studio_file,
    list_studio_messages,
    list_studios,
    mute_member,
    pause_studio,
    post_human_message,
    remove_member,
    rerun_last_turn,
    resume_studio,
    close_studio as close_group_studio,
    delete_studio,
    update_task_card,
    upload_studio_file,
)
from src.group_studio.settings import is_group_studio_enabled
from src.group_studio.stream import format_sse, heartbeat_comment, subscribe
from src.storage.database import (
    get_group_studio_max_event_seq,
    get_group_studio_snapshot,
    list_group_studio_events_after,
)

router = APIRouter()


def require_group_studio_enabled() -> None:
    """写/读工作室 API 在 flag 关闭时一律 404。Settings 不走本路由。"""

    if not is_group_studio_enabled():
        raise group_studio_http_error(404, GROUP_STUDIO_DISABLED)


EnabledDep = Depends(require_group_studio_enabled)


def _gone() -> None:
    """V1 阶段机入口已删除。"""

    raise group_studio_http_error(410, ILLEGAL_TRANSITION)


@router.get("", dependencies=[EnabledDep])
async def list_studios_route(projectId: str = Query(..., alias="projectId")) -> dict:
    """列出当前 Project 工作室（含 done，不含 deleted）。"""

    from src.server.auth.ownership import filter_ids

    studios = list_studios(projectId)
    allowed = set(filter_ids("group_studios", [str(item.get("id") or "") for item in studios]))
    return {"studios": [item for item in studios if str(item.get("id") or "") in allowed]}


@router.post("/from-chat", dependencies=[EnabledDep])
async def create_from_chat(body: GroupStudioFromChatRequest) -> GroupStudio:
    """从 Chat 会话升级或回到未结束房间。立刻 open，不自动开跑。"""

    try:
        return from_chat(body)
    except GroupStudioError as exc:
        raise group_studio_http_error(exc.status_code, exc.code) from exc


@router.get("/by-chat/{session_id}", dependencies=[EnabledDep])
async def get_by_chat_session(session_id: str) -> GroupStudio | None:
    """按 Chat session 探活未结束房间。"""

    return get_studio_by_chat_session(session_id)


@router.get("/{group_id}", dependencies=[EnabledDep])
async def get_studio_snapshot(group_id: str) -> GroupStudio:
    """读取工作室快照（含最近 100 条 messages）。"""

    from src.server.auth.ownership import visible

    if not visible("group_studios", group_id):
        raise group_studio_http_error(404, "not_found")
    try:
        return get_studio(group_id)
    except GroupStudioError as exc:
        raise group_studio_http_error(exc.status_code, exc.code) from exc


@router.get("/{group_id}/available-agents", dependencies=[EnabledDep])
async def list_available_agents_route(group_id: str) -> list[dict[str, str]]:
    """返回当前 Workspace 中可加入该群聊的普通 Agent。"""

    try:
        return list_invitable_agents(group_id)
    except GroupStudioError as exc:
        raise group_studio_http_error(exc.status_code, exc.code) from exc


@router.delete("/{group_id}", dependencies=[EnabledDep], status_code=204)
async def delete_studio_route(group_id: str, projectId: str = Query(..., alias="projectId")) -> None:
    """删除群聊：标 deleted，不再出现在列表。"""

    try:
        delete_studio(group_id, project_id=projectId)
    except GroupStudioError as exc:
        raise group_studio_http_error(exc.status_code, exc.code) from exc


@router.patch("/{group_id}/model", dependencies=[EnabledDep])
async def update_model(group_id: str, body: GroupStudioModelUpdateRequest) -> GroupStudio:
    try:
        return update_studio_model(group_id, body.project_id, body.model_config_id)
    except GroupStudioError as exc:
        raise group_studio_http_error(exc.status_code, exc.code) from exc
    except ValueError as exc:
        raise group_studio_http_error(422, str(exc)) from exc


@router.get("/{group_id}/messages", dependencies=[EnabledDep])
async def list_messages(group_id: str, before: str | None = None, limit: int = 100) -> dict:
    """分页读取更早的群消息。"""

    try:
        return {"messages": list_studio_messages(group_id, before=before, limit=limit)}
    except GroupStudioError as exc:
        raise group_studio_http_error(exc.status_code, exc.code) from exc


@router.get("/{group_id}/events", dependencies=[EnabledDep])
async def stream_events(group_id: str, request: Request, afterSeq: int | None = Query(default=None)):
    """独立 SSE。afterSeq 缺省从当前最大 seq 续，不重放历史。"""

    snapshot = get_group_studio_snapshot(group_id)
    if snapshot is None:
        raise group_studio_http_error(404, "not_found")
    after = int(afterSeq) if afterSeq is not None else get_group_studio_max_event_seq(group_id)

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
        for event in list_group_studio_events_after(group_id, after):
            yield format_sse(event)
        async for live in subscribe(group_id):
            seq = live.get("seq")
            if seq is not None and int(seq) <= after:
                continue
            yield format_sse(live)

    from src.server.auth.presence import iter_with_presence
    from src.server.auth.routes import token_from_request

    return StreamingResponse(
        iter_with_presence(_heartbeat_wrapper(event_stream()), token_from_request(request)),
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


@router.post("/{group_id}/confirm-intake", dependencies=[EnabledDep])
async def confirm_intake_route(group_id: str, body: GroupConfirmIntakeRequest) -> GroupStudio:
    """V1 已删除。"""

    _gone()
    raise AssertionError("unreachable")


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


@router.post("/{group_id}/rerun", dependencies=[EnabledDep])
async def rerun_studio(group_id: str, body: GroupRerunRequest) -> GroupStudio:
    try:
        return rerun_last_turn(group_id, project_id=body.project_id)
    except GroupStudioError as exc:
        raise group_studio_http_error(exc.status_code, exc.code) from exc


@router.post("/{group_id}/interrupt", dependencies=[EnabledDep])
async def interrupt_studio(group_id: str, body: GroupInterruptRequest) -> dict:
    try:
        interrupt_run(group_id, project_id=body.project_id)
    except GroupStudioError as exc:
        raise group_studio_http_error(exc.status_code, exc.code) from exc
    return {"ok": True}


@router.put("/{group_id}/pin", dependencies=[EnabledDep])
@router.put("/{group_id}/task-card", dependencies=[EnabledDep])
async def update_pin_route(group_id: str, body: GroupTaskCardUpdateRequest) -> TaskCard:
    """更新置顶备注。沿用 task-card 路径兼容旧客户端。"""

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
    """V1 已删除。"""

    _gone()
    raise AssertionError("unreachable")


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
    """V1 已删除。"""

    _gone()
    raise AssertionError("unreachable")


@router.post("/{group_id}/start-review", dependencies=[EnabledDep])
async def start_review(group_id: str, body: GroupStartPhaseRequest) -> GroupStudio:
    """V1 已删除。"""

    _gone()
    raise AssertionError("unreachable")


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


@router.get("/{group_id}/files/{artifact_id}/download", dependencies=[EnabledDep])
async def download_file(group_id: str, artifact_id: str):
    """下载已登记的工作室产物，禁止通过请求路径直接读取任意文件。"""

    try:
        artifact = resolve_studio_file(group_id, artifact_id)
    except GroupStudioError as exc:
        raise group_studio_http_error(exc.status_code, exc.code) from exc
    return FileResponse(
        path=str(artifact["absolutePath"]),
        media_type=str(artifact.get("mediaType") or "application/octet-stream"),
        filename=str(artifact.get("title") or "download"),
    )
