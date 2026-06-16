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
from src.config.runtime_settings import get_current_model_options
from src.storage.database import (
    delete_agent as db_delete_agent,
    list_agents as db_list_agents,
    replace_agents,
    upsert_agent,
    list_json_records,
    migrate_json_list_file,
)

router = APIRouter()

DATA_DIR = "data"
AGENTS_REGISTRY_FILE = os.path.join(DATA_DIR, "agents.json")
AGENTS_NAMESPACE = "agents"


class ModelConfig(BaseModel):
    """模型配置"""
    primary: str = Field(default="qwen-plus", description="主模型")
    fallback: list[str] = Field(default_factory=lambda: ["qwen-turbo"], description="降级模型链")
    temperature: float = Field(default=0.7, ge=0, le=2, description="温度参数")
    top_p: float = Field(default=0.8, ge=0, le=1, description="核采样参数")
    max_tokens: int = Field(default=4096, gt=0, description="最大输出 token 数")


class Guardrails(BaseModel):
    """安全边界配置"""
    filesystem_scope: str = Field(default="/", description="文件系统访问范围")
    shell_blacklist: list[str] = Field(default_factory=lambda: ["rm -rf /", "sudo"], description="Shell 命令黑名单")
    network_enabled: bool = Field(default=True, description="是否允许网络访问")
    human_approval: bool = Field(default=False, description="是否启用人工审批")
    tool_permission_mode: str = Field(
        default="accept_edits",
        alias="toolPermissionMode",
        description="工具权限模式：ask_every_time/accept_edits/bypass",
    )
    tool_permission_profile: str = Field(
        default="confirm_destructive",
        alias="toolPermissionProfile",
        description="工具权限配置：read_only/trusted_tools/confirm_destructive/ask_every_time",
    )
    execution_backend: str = Field(
        default="local",
        alias="executionBackend",
        description="工具执行后端：local/docker",
    )
    docker_image: str = Field(
        default="python:3.11-slim",
        alias="dockerImage",
        description="Docker 沙盒基础镜像",
    )
    persistent_sandbox: bool = Field(
        default=False,
        alias="persistentSandbox",
        description="Docker 沙盒是否持久化工作目录",
    )
    timeout_seconds: int = Field(default=300, gt=0, description="工具调用超时（秒）")

    model_config = {"populate_by_name": True}


class PlanConfig(BaseModel):
    """Agent planning configuration."""

    enabled: bool = Field(default=True, description="是否启用计划模型")
    mode: str = Field(default="structured", description="计划模式：lightweight/structured/strict")
    max_steps: int = Field(default=6, ge=1, le=20, alias="maxSteps", description="最大计划步骤数")
    require_plan_before_tools: bool = Field(default=True, alias="requirePlanBeforeTools", description="调用工具前是否必须先形成计划")
    require_user_approval: bool = Field(default=False, alias="requireUserApproval", description="执行计划前是否需要用户确认")
    allow_replanning: bool = Field(default=True, alias="allowReplanning", description="执行中遇到新信息是否允许修订计划")

    model_config = {"populate_by_name": True}


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
    callable_agents: list[str] = Field(default_factory=list, alias="callableAgents", description="可调用的子 Agent ID 列表")
    max_depth: int = Field(default=2, ge=1, le=5, alias="maxDepth", description="最大调用深度")
    memory_enabled: bool = Field(default=True, alias="memoryEnabled", description="是否在运行时注入 Agent 记忆")
    plan_config: PlanConfig = Field(default_factory=PlanConfig, alias="planConfig", description="计划模型配置")
    guardrails: Guardrails = Field(default_factory=Guardrails, description="安全边界配置")

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
    callable_agents: Optional[list[str]] = Field(None, alias="callableAgents")
    max_depth: Optional[int] = Field(None, ge=1, le=5, alias="maxDepth")
    memory_enabled: Optional[bool] = Field(None, alias="memoryEnabled")
    plan_config: Optional[PlanConfig] = Field(None, alias="planConfig")
    guardrails: Optional[Guardrails] = None

    model_config = {"populate_by_name": True}


class AgentResponse(BaseModel):
    """Agent 响应体"""
    id: str
    name: str
    avatar: str
    description: str
    system_prompt: str
    model_config_data: ModelConfig = Field(alias="modelConfig")
    mcp_tools: list[str] = Field(alias="mcpTools")
    knowledge_tools: list[str] = Field(default_factory=list, alias="knowledgeTools")
    skills: list[str]
    tags: list[str]
    callable_agents: list[str] = Field(alias="callableAgents")
    max_depth: int = Field(alias="maxDepth")
    memory_enabled: bool = Field(default=True, alias="memoryEnabled")
    plan_config: PlanConfig = Field(default_factory=PlanConfig, alias="planConfig")
    guardrails: Guardrails
    status: str = "idle"
    created_at: str
    updated_at: str

    model_config = {"populate_by_name": True}


class AgentExportPackage(BaseModel):
    """Portable Agent export package."""

    schema_version: str = Field(default="agent-workstation.agent.v1", alias="schemaVersion")
    exported_at: str = Field(alias="exportedAt")
    agent: AgentResponse

    model_config = {"populate_by_name": True}


class AgentImportRequest(BaseModel):
    """Import one Agent from a portable package."""

    package: AgentExportPackage
    conflict_strategy: str = Field(default="new_id", alias="conflictStrategy")

    model_config = {"populate_by_name": True}


class AgentImportResponse(BaseModel):
    """Result of importing one Agent."""

    agent: AgentResponse
    warnings: list[str] = Field(default_factory=list)


# 内存存储 + JSON 持久化（后续替换为 SQLite）
_agents: dict[str, AgentResponse] = {}


def _load_agents() -> None:
    migrate_json_list_file(AGENTS_NAMESPACE, AGENTS_REGISTRY_FILE)
    records = db_list_agents()
    if not records:
        legacy = list_json_records(AGENTS_NAMESPACE)
        replace_agents({str(item["id"]): item for item in legacy if item.get("id")})
        records = db_list_agents()
    for item in records:
        agent = AgentResponse.model_validate(item)
        _agents[agent.id] = agent


def _save_agents() -> None:
    replace_agents({
        agent.id: agent.model_dump(by_alias=True)
        for agent in _agents.values()
    })


_load_agents()


def get_registered_agent(agent_id: str) -> AgentResponse | None:
    """Return a persisted Agent configuration by id."""
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
        if len(next_tools) == len(agent.mcp_tools):
            continue

        updated_agent = agent.model_copy(update={
            "mcp_tools": next_tools,
            "updated_at": now,
        })
        _agents[agent_id] = updated_agent
        invalidate_agent(agent_id)
        upsert_agent(updated_agent.model_dump(by_alias=True))
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
        changed += 1
    return changed


@router.get("/models")
async def list_models() -> dict:
    """
    获取可用模型列表

    Returns:
        dict: 模型列表（按类别分组）
    """
    options = [
        {"id": model, "name": model, "description": "系统设置中的可用模型"}
        for model in get_current_model_options()
    ]
    return {"primary": options, "fallback": options}


@router.get("", response_model=list[AgentResponse])
async def list_agents() -> list[AgentResponse]:
    """
    获取所有 Agent 列表

    Returns:
        list[AgentResponse]: Agent 列表
    """
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
    agent = AgentResponse.model_validate({
        "id": str(uuid.uuid4()),
        "name": data.name,
        "avatar": data.avatar,
        "description": data.description,
        "system_prompt": data.system_prompt,
        "modelConfig": data.model_config_data,
        "mcpTools": data.mcp_tools,
        "knowledgeTools": data.knowledge_tools,
        "skills": data.skills,
        "tags": data.tags,
        "callableAgents": data.callable_agents,
        "maxDepth": data.max_depth,
        "memoryEnabled": data.memory_enabled,
        "planConfig": data.plan_config,
        "guardrails": data.guardrails,
        "status": "idle",
        "created_at": now,
        "updated_at": now,
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
    if agent_id not in _agents:
        raise HTTPException(status_code=404, detail="Agent not found")
    return _agents[agent_id]


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
    if agent_id not in _agents:
        raise HTTPException(status_code=404, detail="Agent not found")

    existing = _agents[agent_id]
    update_dict = data.model_dump(exclude_unset=True)

    # 处理 alias 字段
    if "model_config_data" in update_dict:
        update_dict["modelConfig"] = update_dict.pop("model_config_data")
    if "mcp_tools" in update_dict:
        update_dict["mcpTools"] = update_dict.pop("mcp_tools")
    if "knowledge_tools" in update_dict:
        update_dict["knowledgeTools"] = update_dict.pop("knowledge_tools")
    if "callable_agents" in update_dict:
        update_dict["callableAgents"] = update_dict.pop("callable_agents")
    if "max_depth" in update_dict:
        update_dict["maxDepth"] = update_dict.pop("max_depth")
    if "memory_enabled" in update_dict:
        update_dict["memoryEnabled"] = update_dict.pop("memory_enabled")
    if "plan_config" in update_dict:
        update_dict["planConfig"] = update_dict.pop("plan_config")

    # 合并更新
    existing_dict = existing.model_dump(by_alias=True)
    existing_dict.update(update_dict)
    existing_dict["updated_at"] = datetime.now(timezone.utc).isoformat()

    updated_agent = AgentResponse(**existing_dict)
    _agents[agent_id] = updated_agent
    invalidate_agent(agent_id)
    upsert_agent(updated_agent.model_dump(by_alias=True))
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
    if agent_id not in _agents:
        raise HTTPException(status_code=404, detail="Agent not found")
    del _agents[agent_id]
    invalidate_agent(agent_id)
    db_delete_agent(agent_id)
