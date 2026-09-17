"""Subagent 定义管理 API。"""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from src.storage.database import (
    DEFAULT_PROJECT_ID,
    count_subagent_transcript_items,
    get_project,
    get_subagent_invocation,
    list_subagent_invocations,
    list_subagent_transcript_items,
)
from src.subagents.registry import load_subagent_catalog
from src.subagents.supervisor import get_subagent_supervisor


router = APIRouter()


@router.get("")
async def list_subagents(
    project_id: Annotated[str, Query(alias="projectId")] = DEFAULT_PROJECT_ID,
) -> dict:
    """列出当前工作区加载到的 Subagent 文件定义。"""

    project = get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    catalog = load_subagent_catalog(workspace_dir=str(project["rootPath"]))
    return {
        "definitions": [
            {
                "name": item.name,
                "description": item.description,
                "scope": item.scope,
                "path": item.path,
                "tools": item.tools,
                "disallowedTools": item.disallowed_tools,
                "model": item.model,
                "permissionMode": item.permission_mode,
                "maxTurns": item.max_turns,
                "skills": item.skills,
                "mcpServers": item.mcp_servers,
                "color": item.color,
                "metadata": item.metadata,
                "contentHash": item.content_hash,
            }
            for item in sorted(catalog.definitions.values(), key=lambda definition: definition.name)
        ],
        "errors": [
            {
                "scope": error.scope,
                "path": error.path,
                "message": error.message,
            }
            for error in catalog.errors
        ],
        "warnings": catalog.warnings,
        "fingerprint": catalog.fingerprint,
    }


@router.get("/invocations")
async def list_invocations(
    runId: str = "",
    sessionId: str = "",
    projectId: str = DEFAULT_PROJECT_ID,
    limit: int = 100,
) -> dict:
    """列出 Subagent invocation 审计记录。"""

    return {
        "items": list_subagent_invocations(
            run_id=runId or None,
            session_id=sessionId or None,
            project_id=projectId,
            limit=max(1, min(int(limit or 100), 500)),
        )
    }


def _get_project_invocation(invocation_id: str, project_id: str) -> dict:
    """按 invocation id 读取 task，并保持 Project 隔离。"""

    invocation = get_subagent_invocation(invocation_id)
    if invocation is None or invocation.get("projectId") != project_id:
        raise HTTPException(status_code=404, detail="Subagent invocation not found")
    return invocation


@router.get("/invocations/{invocation_id}")
async def get_invocation(
    invocation_id: str,
    projectId: str = DEFAULT_PROJECT_ID,
) -> dict:
    """读取一个稳定 Subagent task identity 的审计详情。"""

    return _get_project_invocation(invocation_id, projectId)


@router.get("/invocations/{invocation_id}/transcript")
async def get_invocation_transcript(
    invocation_id: str,
    projectId: str = DEFAULT_PROJECT_ID,
    limit: int = 500,
    offset: int = 0,
) -> dict:
    """读取 task 的规范化 transcript，供后续 detail drawer 复用。"""

    _get_project_invocation(invocation_id, projectId)
    safe_limit = max(1, min(int(limit or 100), 2_000))
    safe_offset = max(0, int(offset or 0))
    items = list_subagent_transcript_items(invocation_id, limit=safe_limit, offset=safe_offset)
    total = count_subagent_transcript_items(invocation_id)
    return {
        "items": items,
        "total": total,
        "limit": safe_limit,
        "offset": safe_offset,
        "hasMore": safe_offset + len(items) < total,
    }


@router.post("/invocations/{invocation_id}/cancel")
async def cancel_invocation(
    invocation_id: str,
    projectId: str = DEFAULT_PROJECT_ID,
) -> dict:
    """请求取消当前进程中由 Supervisor 执行的 Subagent task。"""

    invocation = _get_project_invocation(invocation_id, projectId)
    if invocation.get("status") not in {"queued", "running"}:
        raise HTTPException(status_code=409, detail="Only queued or running Subagent tasks can be cancelled")
    accepted = await get_subagent_supervisor().cancel(invocation_id)
    if not accepted:
        raise HTTPException(status_code=409, detail="Subagent task is not active in this backend process")
    return {"accepted": True, "invocationId": invocation_id}
