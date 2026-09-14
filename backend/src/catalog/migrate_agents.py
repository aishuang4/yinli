"""把 sqlite `agents` 表一次性导出为 profile 文件。"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from src.agents.system_agents import is_system_agent_id
from src.catalog.agent_models import AgentResponse
from src.catalog.loader import get_registered_agent, import_sqlite_agent_record, invalidate_catalog
from src.catalog.paths import profile_path, system_profile_path
from src.catalog.profiles import dump_profile_markdown
from src.catalog.system_defaults import (
    DEFAULT_AGENT_FINAL_DELIVERY_RULES,
    DEFAULT_AGENT_PROMPT_VERSION_TAG,
    build_default_agent,
    build_default_websearch_job_agent,
    utc_now,
)

logger = logging.getLogger(__name__)
CATALOG_NAMESPACE = "catalog"
MIGRATION_KEY = "agent_migration_v1"


def is_agent_catalog_migrated() -> bool:
    """marker.status == done。"""

    from src.storage.database import get_json_record

    marker = get_json_record(CATALOG_NAMESPACE, MIGRATION_KEY) or {}
    return str(marker.get("status") or "") == "done"


def _write_marker(payload: dict[str, Any]) -> None:
    from src.storage.database import upsert_json_record

    upsert_json_record(CATALOG_NAMESPACE, MIGRATION_KEY, payload)


def _agent_from_row(row: dict[str, Any]) -> AgentResponse:
    return AgentResponse.model_validate({
        "id": row["id"],
        "name": row.get("name") or row["id"],
        "avatar": row.get("avatar", ""),
        "description": row.get("description", ""),
        "system_prompt": row.get("system_prompt") or "You are a helpful assistant.",
        "modelConfig": row.get("modelConfig") or {},
        "mcpTools": row.get("mcpTools") or [],
        "knowledgeTools": row.get("knowledgeTools") or [],
        "skills": row.get("skills") or [],
        "tags": row.get("tags") or [],
        "planConfig": row.get("planConfig") or {},
        "guardrails": row.get("guardrails") or {},
        "status": "idle",
        "created_at": row.get("created_at") or utc_now(),
        "updated_at": row.get("updated_at") or utc_now(),
        "workspaceId": row.get("workspaceId") or "default",
        "role": row.get("role") or "chat",
        "jobToolkit": row.get("jobToolkit") or {},
    })


def _upgrade_default_row(agent: AgentResponse) -> AgentResponse:
    if DEFAULT_AGENT_PROMPT_VERSION_TAG in agent.tags:
        return agent
    prompt = agent.system_prompt.rstrip()
    if DEFAULT_AGENT_FINAL_DELIVERY_RULES not in prompt:
        prompt = "\n\n".join([prompt, DEFAULT_AGENT_FINAL_DELIVERY_RULES]).strip()
    return agent.model_copy(update={
        "system_prompt": prompt,
        "tags": [*agent.tags, DEFAULT_AGENT_PROMPT_VERSION_TAG],
    })


def _template_signature(agent: AgentResponse) -> tuple:
    return (
        agent.system_prompt,
        tuple(agent.skills),
        tuple(agent.mcp_tools),
        tuple(agent.knowledge_tools),
        agent.model_config_data.max_react_iters,
        agent.guardrails.model_dump(),
        agent.role,
    )


def _export_file(path, agent: AgentResponse) -> str:
    from src.catalog.paths import atomic_write_text

    if path.exists():
        current = path.read_text(encoding="utf-8")
        incoming = dump_profile_markdown(agent)
        if current == incoming:
            return "skipped"
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        conflict = path.with_name(f"{path.stem}.conflict-{stamp}.md")
        atomic_write_text(conflict, incoming)
        logger.warning("迁移冲突，保留已有文件 %s，导出副本 %s", path, conflict)
        return "conflict"
    from src.catalog.profiles import write_profile_file

    write_profile_file(path, agent)
    return "exported"


def ensure_agent_file_catalog() -> None:
    """启动时调用：已迁移则只读文件；否则导出 sqlite 后删表。"""

    from src.storage.database import list_agents, replace_agents

    if is_agent_catalog_migrated():
        invalidate_catalog()
        return
    exported = 0
    overlay_default = False
    overlay_websearch = False
    try:
        rows = list_agents()
        exported_ids: list[str] = []
        default_template = build_default_agent()
        websearch_template = build_default_websearch_job_agent()
        for row in rows:
            agent = _agent_from_row(row)
            if agent.id == "default":
                agent = _upgrade_default_row(agent)
                if _template_signature(agent) != _template_signature(default_template):
                    _export_file(system_profile_path(agent.id), agent.model_copy(update={
                        "source_kind": "system_overlay",
                        "workspace_id": "default",
                    }))
                    overlay_default = True
                continue
            if is_system_agent_id(agent.id):
                if _template_signature(agent) != _template_signature(websearch_template):
                    _export_file(system_profile_path(agent.id), agent.model_copy(update={
                        "source_kind": "system_overlay",
                        "workspace_id": "default",
                    }))
                    overlay_websearch = True
                continue
            result = _export_file(
                profile_path(agent.workspace_id, agent.id),
                agent.model_copy(update={"source_kind": "profile"}),
            )
            if result == "exported":
                exported += 1
            exported_ids.append(agent.id)

        invalidate_catalog()
        missing = [agent_id for agent_id in exported_ids if get_registered_agent(agent_id) is None]
        if missing:
            raise RuntimeError(f"迁移抽查失败，无法读回: {missing}")

        replace_agents({})
        _write_marker({
            "schemaVersion": 1,
            "status": "done",
            "migratedAt": utc_now(),
            "exported": exported,
            "deletedRows": len(rows),
            "overlayDefault": overlay_default,
            "overlayWebsearch": overlay_websearch,
        })
        invalidate_catalog()
    except Exception:
        try:
            _write_marker({"schemaVersion": 1, "status": "failed", "migratedAt": utc_now()})
        except Exception:
            logger.exception("无法写入失败迁移标记")
        raise
