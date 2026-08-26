"""群聊工作室独立路由。

flag 关闭时全部 404 `group_studio_disabled`。
from-chat / GET / by-chat 已实现；其余写接口仍 501。
不渲染 Chat 入口，不启动 LLM。
"""

from fastapi import APIRouter, Depends

from src.group_studio.errors import (
    GROUP_STUDIO_DISABLED,
    GROUP_STUDIO_NOT_IMPLEMENTED,
    GroupStudioError,
    group_studio_http_error,
)
from src.group_studio.models import (
    GroupApprovalDecisionRequest,
    GroupCloseRequest,
    GroupConfirmRestatementRequest,
    GroupInterruptRequest,
    GroupMemberCreateRequest,
    GroupMemberMuteRequest,
    GroupMemberRemoveRequest,
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
    from_chat,
    get_studio,
    get_studio_by_chat_session,
    list_studio_messages,
    prepare_start_execution,
    update_task_card,
)
from src.group_studio.settings import is_group_studio_enabled

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
async def stream_events(group_id: str, afterSeq: int | None = None) -> None:
    """独立 SSE。契约见 docs/group-studio-event-contract.md。"""

    _not_implemented()


@router.post("/{group_id}/pause", dependencies=[EnabledDep])
async def pause_studio(group_id: str, _body: GroupPauseRequest) -> None:
    _not_implemented()


@router.post("/{group_id}/resume", dependencies=[EnabledDep])
async def resume_studio(group_id: str, _body: GroupResumeRequest) -> None:
    _not_implemented()


@router.post("/{group_id}/messages", dependencies=[EnabledDep])
async def post_message(group_id: str, _body: GroupMessageCreateRequest) -> None:
    _not_implemented()


@router.post("/{group_id}/interrupt", dependencies=[EnabledDep])
async def interrupt_studio(group_id: str, _body: GroupInterruptRequest) -> None:
    _not_implemented()


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
async def mute_member(group_id: str, member_id: str, _body: GroupMemberMuteRequest) -> None:
    _not_implemented()


@router.post("/{group_id}/members/{member_id}/remove", dependencies=[EnabledDep])
async def remove_member(group_id: str, member_id: str, _body: GroupMemberRemoveRequest) -> None:
    _not_implemented()


@router.post("/{group_id}/close", dependencies=[EnabledDep])
async def close_studio(group_id: str, _body: GroupCloseRequest) -> None:
    _not_implemented()


@router.post("/{group_id}/confirm-restatement", dependencies=[EnabledDep])
async def confirm_restatement(group_id: str, _body: GroupConfirmRestatementRequest) -> None:
    _not_implemented()


@router.post("/{group_id}/approvals", dependencies=[EnabledDep])
async def decide_approval(group_id: str, _body: GroupApprovalDecisionRequest) -> None:
    _not_implemented()


@router.post("/{group_id}/start-execution", dependencies=[EnabledDep])
async def start_execution(group_id: str, body: GroupStartPhaseRequest) -> None:
    try:
        prepare_start_execution(group_id, body.project_id)
    except GroupStudioError as exc:
        raise group_studio_http_error(exc.status_code, exc.code) from exc
    _not_implemented()


@router.post("/{group_id}/start-review", dependencies=[EnabledDep])
async def start_review(group_id: str, _body: GroupStartPhaseRequest) -> None:
    _not_implemented()


@router.post("/{group_id}/files", dependencies=[EnabledDep])
async def upload_file(group_id: str) -> None:
    _not_implemented()


@router.get("/{group_id}/files", dependencies=[EnabledDep])
async def list_files(group_id: str) -> None:
    _not_implemented()
