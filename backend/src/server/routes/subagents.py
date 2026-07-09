"""Subagent 定义管理 API。"""

from fastapi import APIRouter

from src.storage.database import list_subagent_invocations
from src.subagents.registry import load_subagent_catalog


router = APIRouter()


@router.get("")
async def list_subagents() -> dict:
    """列出当前工作区加载到的 Subagent 文件定义。"""

    catalog = load_subagent_catalog()
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
async def list_invocations(runId: str = "", sessionId: str = "", limit: int = 100) -> dict:
    """列出 Subagent invocation 审计记录。"""

    return {
        "items": list_subagent_invocations(
            run_id=runId or None,
            session_id=sessionId or None,
            limit=max(1, min(int(limit or 100), 500)),
        )
    }
