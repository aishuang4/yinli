"""
Agent 管理路由

提供 Agent 的 CRUD 操作：
- GET  /api/agents        获取所有 Agent 列表
- POST /api/agents        创建新 Agent
- GET  /api/agents/{id}   获取单个 Agent 详情
- PUT  /api/agents/{id}   更新 Agent 配置
- DELETE /api/agents/{id} 删除 Agent
- GET  /api/models        获取可用模型列表
"""

import os
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime, timezone
import uuid

from src.agents.factory import invalidate_agent
from src.agents.system_agents import (
    DEFAULT_WEBSEARCH_JOB_AGENT_ID,
    is_default_chat_agent_id,
    is_default_websearch_job_agent_id,
    is_system_agent_id,
)
from src.catalog.agent_models import (
    AgentExportPackage,
    AgentImportRequest,
    AgentImportResponse,
    AgentResponse,
    Guardrails,
    ModelConfig,
    PlanConfig,
)
from src.catalog.system_defaults import (
    DEFAULT_AGENT_FINAL_DELIVERY_RULES,
    DEFAULT_AGENT_PROMPT_VERSION_TAG,
    build_default_agent,
    build_default_websearch_job_agent,
)
from src.scheduler.job_toolkit import (
    CHAT_ROLE,
    JOB_ROLE,
    JobAgentError,
    JobToolkit,
    bound_resource_ids,
    empty_job_toolkit,
    parse_agent_role,
    parse_job_toolkit,
    prune_job_toolkit,
    validate_job_agent_config,
)
from src.scheduler.service import ScheduledJobService

router = APIRouter()
_job_service = ScheduledJobService()


def _pause_jobs_for_removed_agent_skills(agent_id: str, skill_ids: set[str]) -> None:
    """Agent 不再绑定某 Skill 时，暂停仍冻结该 Skill 的 Job。"""

    for skill_id in skill_ids:
        _job_service.pause_for_resource_change(
            "skill_unbound",
            agent_id=agent_id,
            skill_id=skill_id,
        )


def _pause_jobs_for_agent_capability_change(old_agent: "AgentResponse", new_agent: "AgentResponse") -> None:
    """MCP/HTTP 解绑或权限降级时按合同暂停 Job。"""

    from src.scheduler.unattended_policy import agent_approval_policy

    removed_mcp = set(old_agent.mcp_tools) - set(new_agent.mcp_tools)
    removed_knowledge = set(old_agent.knowledge_tools) - set(new_agent.knowledge_tools)
    old_policy = agent_approval_policy(old_agent)
    new_policy = agent_approval_policy(new_agent)
    old_net = bool(getattr(old_agent.guardrails, "network_enabled", True))
    new_net = bool(getattr(new_agent.guardrails, "network_enabled", True))
    permission_revoked = (
        old_policy != new_policy
        or (old_net and not new_net)
    )
    if removed_mcp or removed_knowledge:
        _job_service.pause_jobs_for_removed_agent_tools(
            new_agent.id,
            mcp_ids=removed_mcp,
            knowledge_ids=removed_knowledge,
        )
    if permission_revoked:
        _job_service.pause_jobs_for_removed_agent_tools(
            new_agent.id,
            permission_revoked=True,
        )


AGENTS_NAMESPACE = "agents"


class AgentCreate(BaseModel):
    """创建 Agent 的请求体"""
    name: str = Field(..., min_length=1, max_length=50, description="Agent 名称")
    avatar: str = Field(default="", description="头像 URL")
    description: str = Field(default="", max_length=200, description="一句话描述")
    system_prompt: str = Field(default="You are a helpful assistant.", description="系统提示词")
    model_config_data: ModelConfig = Field(default_factory=ModelConfig, alias="modelConfig", description="模型配置")
    mcp_tools: list[str] = Field(default_factory=list, alias="mcpTools", description="MCP 工具 ID 列表")
    knowledge_tools: list[str] = Field(default_factory=list, alias="knowledgeTools", description="知识库 HTTP 工具 ID 列表")
    skills: list[str] = Field(default_factory=list, description="Skills ID 列表")
    tags: list[str] = Field(default_factory=list, description="标签")
    plan_config: PlanConfig = Field(default_factory=PlanConfig, alias="planConfig", description="历史兼容字段：旧计划模型配置")
    guardrails: Guardrails = Field(default_factory=Guardrails, description="安全边界配置")
    role: str = Field(default="chat", description="chat=对话助手，job=定时任务执行者")
    job_toolkit: JobToolkit = Field(default_factory=JobToolkit, alias="jobToolkit")

    model_config = {"populate_by_name": True}


class AgentUpdate(BaseModel):
    """更新 Agent 的请求体（所有字段可选）"""
    name: Optional[str] = Field(None, min_length=1, max_length=50)
    avatar: Optional[str] = None
    description: Optional[str] = Field(None, max_length=200)
    system_prompt: Optional[str] = None
    model_config_data: Optional[ModelConfig] = Field(None, alias="modelConfig")
    mcp_tools: Optional[list[str]] = Field(None, alias="mcpTools")
    knowledge_tools: Optional[list[str]] = Field(None, alias="knowledgeTools")
    skills: Optional[list[str]] = None
    tags: Optional[list[str]] = None
    plan_config: Optional[PlanConfig] = Field(None, alias="planConfig")
    guardrails: Optional[Guardrails] = None
    role: Optional[str] = None
    job_toolkit: Optional[JobToolkit] = Field(None, alias="jobToolkit")

    model_config = {"populate_by_name": True}


# 测试可 monkeypatch 此字典；生产路径按租户懒加载。
_agents: dict[str, AgentResponse] = {}


def _build_default_agent() -> AgentResponse:
    """兼容测试与旧调用点。"""

    return build_default_agent()


def _build_default_websearch_job_agent() -> AgentResponse:
    """兼容测试与旧调用点。"""

    return build_default_websearch_job_agent()


def upsert_agent(data: dict) -> None:
    """写入 catalog 文件。测试可 patch；生产 CRUD 也走此函数以便测试拦截。"""

    from src.catalog.loader import save_agent

    saved = save_agent(AgentResponse.model_validate(data))
    _agents[saved.id] = saved


def _ensure_default_agent() -> None:
    """为历史配置补齐默认 Agent。"""

    existing = _agents.get("default")
    if existing is not None:
        _upgrade_default_agent_prompt(existing)
        return
    default_agent = _build_default_agent()
    _agents[default_agent.id] = default_agent
    upsert_agent(default_agent.model_dump(by_alias=True))


def _ensure_default_websearch_job_agent() -> None:
    """幂等补齐系统 WebSearch Job Agent。"""

    if DEFAULT_WEBSEARCH_JOB_AGENT_ID in _agents:
        return
    agent = _build_default_websearch_job_agent()
    _agents[agent.id] = agent
    upsert_agent(agent.model_dump(by_alias=True))


def _ensure_system_agents() -> None:
    """统一确保系统 Chat Agent 与 Job Agent 存在。"""

    _ensure_default_agent()
    _ensure_default_websearch_job_agent()


def _upgrade_default_agent_prompt(agent: AgentResponse) -> None:
    """为已有默认 Agent 一次性补齐最终交付规则，保留用户原有提示词。"""

    if DEFAULT_AGENT_PROMPT_VERSION_TAG in agent.tags:
        return
    prompt = agent.system_prompt.rstrip()
    if DEFAULT_AGENT_FINAL_DELIVERY_RULES not in prompt:
        prompt = "\n\n".join([prompt, DEFAULT_AGENT_FINAL_DELIVERY_RULES]).strip()
    updated_agent = agent.model_copy(update={
        "system_prompt": prompt,
        "tags": [*agent.tags, DEFAULT_AGENT_PROMPT_VERSION_TAG],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })
    _agents[agent.id] = updated_agent
    invalidate_agent(agent.id)
    upsert_agent(updated_agent.model_dump(by_alias=True))


def _apply_job_agent_fields(
    *,
    agent_id: str,
    role: str,
    job_toolkit: JobToolkit | dict | None,
    guardrails: Guardrails,
    mcp_tools: list[str],
    knowledge_tools: list[str],
    skills: list[str],
) -> tuple[str, JobToolkit, Guardrails, list[str], list[str], list[str]]:
    """校验角色并同步绑定字段。Job Agent 强制可信工具。"""

    parsed_role = parse_agent_role(role)
    toolkit = parse_job_toolkit(job_toolkit)
    try:
        toolkit = validate_job_agent_config(
            agent_id=agent_id,
            role=parsed_role,
            toolkit=toolkit,
            guardrails=guardrails,
        )
    except JobAgentError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if parsed_role == JOB_ROLE:
        bound = bound_resource_ids(toolkit)
        mcp_tools = list(bound["mcp"])
        knowledge_tools = list(dict.fromkeys([*bound["http"], *bound["knowledge"]]))
        skills = list(bound["skill"])
        guardrails = guardrails.model_copy(update={
            "human_approval": False,
            "tool_permission_profile": "trusted_tools",
            "tool_permission_mode": "bypass",
        })
    else:
        toolkit = empty_job_toolkit()
    return parsed_role, toolkit, guardrails, mcp_tools, knowledge_tools, skills


def _sync_agents_from_catalog() -> None:
    """用 catalog 扫描结果填充 `_agents`，不替换字典对象。"""

    from src.catalog.loader import list_registered_agents

    loaded = {agent.id: agent for agent in list_registered_agents()}
    _agents.clear()
    _agents.update(loaded)


def _load_agents() -> None:
    from src.catalog.migrate_agents import ensure_agent_file_catalog
    from src.catalog.skill_seed import seed_yinli_catalog_skill

    seed_yinli_catalog_skill()
    ensure_agent_file_catalog()
    _sync_agents_from_catalog()
    _ensure_system_agents()
    _sync_agents_from_catalog()


def _save_agents() -> None:
    for agent in list(_agents.values()):
        upsert_agent(agent.model_dump(by_alias=True))


def _install_agent_registry() -> None:
    """第一次访问当前租户时再加载，避免 import 阶段没有租户。"""

    global _agents
    from src.tenancy.registry import TenantDictProxy, TenantScoped

    scoped: TenantScoped[dict] = TenantScoped(dict)
    _agents = TenantDictProxy(scoped, _load_agents)  # type: ignore[assignment]


_install_agent_registry()


def get_registered_agent(agent_id: str) -> AgentResponse | None:
    """catalog 优先，保证 Chat 下一轮读到刚 Edit 的 profile。"""

    from src.catalog.loader import get_registered_agent as catalog_get

    loaded = catalog_get(str(agent_id or ""))
    if loaded is not None:
        _agents[loaded.id] = loaded
        return loaded
    return _agents.get(agent_id)


def list_registered_agents() -> list[AgentResponse]:
    """Return persisted Agent configurations for internal matching logic."""
    return list(_agents.values())


def remove_mcp_tools_from_agents(tool_ids: set[str]) -> int:
    """Remove deleted MCP tool ids from all persisted Agents."""

    if not tool_ids:
        return 0

    changed = 0
    now = datetime.now(timezone.utc).isoformat()
    for agent_id, agent in list(_agents.items()):
        next_tools = [tool_id for tool_id in agent.mcp_tools if tool_id not in tool_ids]
        next_toolkit = prune_job_toolkit(agent.job_toolkit, mcp_ids=tool_ids)
        if len(next_tools) == len(agent.mcp_tools) and next_toolkit == agent.job_toolkit:
            continue

        updated_agent = agent.model_copy(update={
            "mcp_tools": next_tools,
            "job_toolkit": next_toolkit,
            "updated_at": now,
        })
        _agents[agent_id] = updated_agent
        invalidate_agent(agent_id)
        upsert_agent(updated_agent.model_dump(by_alias=True))
        _job_service.pause_jobs_for_removed_agent_tools(agent_id, mcp_ids=tool_ids)
        changed += 1
    return changed


def remove_skills_from_agents(skill_ids: set[str]) -> int:
    """Remove deleted Skill ids from all persisted Agents."""

    if not skill_ids:
        return 0

    changed = 0
    now = datetime.now(timezone.utc).isoformat()
    for agent_id, agent in list(_agents.items()):
        next_skills = [skill_id for skill_id in agent.skills if skill_id not in skill_ids]
        if len(next_skills) == len(agent.skills):
            continue

        updated_agent = agent.model_copy(update={
            "skills": next_skills,
            "updated_at": now,
        })
        _agents[agent_id] = updated_agent
        invalidate_agent(agent_id)
        upsert_agent(updated_agent.model_dump(by_alias=True))
        _pause_jobs_for_removed_agent_skills(agent_id, skill_ids)
        changed += 1
    return changed


@router.get("", response_model=list[AgentResponse])
async def list_agents() -> list[AgentResponse]:
    """
    获取所有 Agent 列表

    Returns:
        list[AgentResponse]: Agent 列表
    """
    _sync_agents_from_catalog()
    return list(_agents.values())


@router.get("/{agent_id}/export", response_model=AgentExportPackage)
async def export_agent(agent_id: str) -> AgentExportPackage:
    """Export one Agent as a portable JSON package."""
    if agent_id not in _agents:
        raise HTTPException(status_code=404, detail="Agent not found")
    return AgentExportPackage(
        exportedAt=datetime.now(timezone.utc).isoformat(),
        agent=_agents[agent_id],
    )


@router.post("/import", response_model=AgentImportResponse, status_code=201)
async def import_agent(data: AgentImportRequest) -> AgentImportResponse:
    """Import one Agent package, creating a new Agent by default."""
    if data.package.schema_version != "agent-workstation.agent.v1":
        raise HTTPException(status_code=400, detail="Unsupported Agent export schema")

    source = data.package.agent
    warnings: list[str] = []
    now = datetime.now(timezone.utc).isoformat()
    next_id = source.id
    if data.conflict_strategy not in {"new_id", "keep_id"}:
        raise HTTPException(status_code=400, detail="Unsupported conflict strategy")
    if data.conflict_strategy == "new_id" or next_id in _agents:
        if next_id in _agents:
            warnings.append("Agent ID already exists; imported with a new ID.")
        next_id = str(uuid.uuid4())

    next_name = source.name
    if any(agent.name == next_name for agent in _agents.values()):
        next_name = f"{source.name} Copy"
        warnings.append("Agent name already exists; imported name was suffixed with Copy.")

    imported = source.model_copy(update={
        "id": next_id,
        "name": next_name,
        "status": "idle",
        "created_at": now,
        "updated_at": now,
    })
    _agents[imported.id] = imported
    upsert_agent(imported.model_dump(by_alias=True))
    return AgentImportResponse(agent=imported, warnings=warnings)


@router.post("", response_model=AgentResponse, status_code=201)
async def create_agent(data: AgentCreate) -> AgentResponse:
    """
    创建新 Agent

    Args:
        data: Agent 创建参数

    Returns:
        AgentResponse: 创建的 Agent 信息
    """
    now = datetime.now(timezone.utc).isoformat()
    next_id = str(uuid.uuid4())
    role, toolkit, guardrails, mcp_tools, knowledge_tools, skills = _apply_job_agent_fields(
        agent_id=next_id,
        role=data.role,
        job_toolkit=data.job_toolkit,
        guardrails=data.guardrails,
        mcp_tools=list(data.mcp_tools),
        knowledge_tools=list(data.knowledge_tools),
        skills=list(data.skills),
    )
    agent = AgentResponse.model_validate({
        "id": next_id,
        "name": data.name,
        "avatar": data.avatar,
        "description": data.description,
        "system_prompt": data.system_prompt,
        "modelConfig": data.model_config_data,
        "mcpTools": mcp_tools,
        "knowledgeTools": knowledge_tools,
        "skills": skills,
        "tags": data.tags,
        "planConfig": data.plan_config,
        "guardrails": guardrails,
        "status": "idle",
        "created_at": now,
        "updated_at": now,
        "role": role,
        "jobToolkit": toolkit.model_dump(),
    })
    _agents[agent.id] = agent
    upsert_agent(agent.model_dump(by_alias=True))
    return agent


@router.get("/{agent_id}", response_model=AgentResponse)
async def get_agent(agent_id: str) -> AgentResponse:
    """
    获取单个 Agent 详情

    Args:
        agent_id: Agent UUID

    Returns:
        AgentResponse: Agent 详情

    Raises:
        HTTPException: Agent 不存在时返回 404
    """
    agent = get_registered_agent(agent_id)
    if agent is None:
        from src.catalog.loader import get_profile_invalid_error

        invalid = get_profile_invalid_error(agent_id)
        if invalid:
            raise HTTPException(status_code=409, detail=f"agent_profile_invalid: {invalid}")
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


@router.put("/{agent_id}", response_model=AgentResponse)
async def update_agent(agent_id: str, data: AgentUpdate) -> AgentResponse:
    """
    更新 Agent 配置

    Args:
        agent_id: Agent UUID
        data: 更新参数（部分更新）

    Returns:
        AgentResponse: 更新后的 Agent 信息

    Raises:
        HTTPException: Agent 不存在时返回 404
    """
    existing = get_registered_agent(agent_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    update_dict = data.model_dump(exclude_unset=True)

    # 处理 alias 字段
    if "model_config_data" in update_dict:
        update_dict["modelConfig"] = update_dict.pop("model_config_data")
    if "mcp_tools" in update_dict:
        update_dict["mcpTools"] = update_dict.pop("mcp_tools")
    if "knowledge_tools" in update_dict:
        update_dict["knowledgeTools"] = update_dict.pop("knowledge_tools")
    if "plan_config" in update_dict:
        update_dict["planConfig"] = update_dict.pop("plan_config")
    if "job_toolkit" in update_dict:
        update_dict["jobToolkit"] = update_dict.pop("job_toolkit")

    # 合并更新
    existing_dict = existing.model_dump(by_alias=True)
    existing_dict.update(update_dict)
    existing_dict["updated_at"] = datetime.now(timezone.utc).isoformat()
    if is_default_chat_agent_id(agent_id):
        existing_dict["role"] = CHAT_ROLE
        existing_dict["jobToolkit"] = empty_job_toolkit().model_dump()
    else:
        if is_default_websearch_job_agent_id(agent_id):
            existing_dict["role"] = JOB_ROLE
            guardrails = Guardrails.model_validate(existing_dict.get("guardrails") or {})
            guardrails = guardrails.model_copy(update={
                "network_enabled": True,
                "human_approval": False,
                "tool_permission_profile": "trusted_tools",
                "tool_permission_mode": "bypass",
            })
            existing_dict["guardrails"] = guardrails
        role, toolkit, guardrails, mcp_tools, knowledge_tools, skills = _apply_job_agent_fields(
            agent_id=agent_id,
            role=str(existing_dict.get("role") or CHAT_ROLE),
            job_toolkit=existing_dict.get("jobToolkit"),
            guardrails=guardrails if isinstance(existing_dict.get("guardrails"), Guardrails) else Guardrails.model_validate(existing_dict.get("guardrails") or {}),
            mcp_tools=list(existing_dict.get("mcpTools") or []),
            knowledge_tools=list(existing_dict.get("knowledgeTools") or []),
            skills=list(existing_dict.get("skills") or []),
        )
        existing_dict["role"] = role
        existing_dict["jobToolkit"] = toolkit.model_dump()
        existing_dict["guardrails"] = guardrails
        existing_dict["mcpTools"] = mcp_tools
        existing_dict["knowledgeTools"] = knowledge_tools
        existing_dict["skills"] = skills

    updated_agent = AgentResponse(**existing_dict)
    removed_skills = set()
    if "skills" in update_dict:
        removed_skills = set(existing.skills) - set(updated_agent.skills)
    _agents[agent_id] = updated_agent
    invalidate_agent(agent_id)
    upsert_agent(updated_agent.model_dump(by_alias=True))
    if removed_skills:
        _pause_jobs_for_removed_agent_skills(agent_id, removed_skills)
    _pause_jobs_for_agent_capability_change(existing, updated_agent)
    if existing.role == JOB_ROLE and updated_agent.role != JOB_ROLE:
        _job_service.pause_for_resource_change("needs_job_update", agent_id=agent_id)
    return updated_agent


def remove_knowledge_tools_from_agents(tool_ids: set[str]) -> int:
    """Remove deleted knowledge tool ids from all persisted Agents."""

    if not tool_ids:
        return 0

    changed = 0
    now = datetime.now(timezone.utc).isoformat()
    for agent_id, agent in list(_agents.items()):
        next_tools = [tool_id for tool_id in agent.knowledge_tools if tool_id not in tool_ids]
        if len(next_tools) == len(agent.knowledge_tools):
            continue

        updated_agent = agent.model_copy(update={
            "knowledge_tools": next_tools,
            "updated_at": now,
        })
        _agents[agent_id] = updated_agent
        invalidate_agent(agent_id)
        upsert_agent(updated_agent.model_dump(by_alias=True))
        _job_service.pause_jobs_for_removed_agent_tools(
            agent_id, knowledge_ids=tool_ids,
        )
        changed += 1
    return changed


@router.delete("/{agent_id}", status_code=204)
async def delete_agent(agent_id: str) -> None:
    """
    删除 Agent

    Args:
        agent_id: Agent UUID

    Raises:
        HTTPException: Agent 不存在时返回 404
    """
    if is_system_agent_id(agent_id):
        raise HTTPException(status_code=403, detail="系统 Agent 不能删除，只能修改配置")
    if get_registered_agent(agent_id) is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    _job_service.pause_for_resource_change("agent_missing", agent_id=agent_id)
    del _agents[agent_id]
    invalidate_agent(agent_id)
    from src.catalog.loader import delete_agent as catalog_delete_agent

    try:
        catalog_delete_agent(agent_id)
    except (FileNotFoundError, PermissionError):
        pass
