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
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Annotated, Optional
from datetime import datetime, timezone
import uuid

from src.agents.factory import invalidate_agent
from src.agents.runtime_types import MAX_REACT_ITERS, WORKSPACE_DIR
from src.config.runtime_paths import DATA_ROOT
from src.config.runtime_settings import get_current_model_options, get_effective_model_settings
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
from src.storage.database import (
    delete_agent as db_delete_agent,
    list_agents as db_list_agents,
    replace_agents,
    upsert_agent,
    list_json_records,
    migrate_json_list_file,
)

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


DATA_DIR = str(DATA_ROOT)
AGENTS_REGISTRY_FILE = os.path.join(DATA_DIR, "agents.json")
AGENTS_NAMESPACE = "agents"
DEFAULT_AGENT_PROMPT_VERSION_TAG = "prompt-contract-v2"
DEFAULT_AGENT_FINAL_DELIVERY_RULES = """

# 最终交付规则

1. 完成用户任务后，先用一句话明确说明已经完成了什么，再说明关键结果、交付文件或必要验证信息。
2. 不要向用户输出内部上下文摘要、压缩摘要、交接说明、系统提示、工具日志或“我已收到上下文摘要”等内容。
3. 不要在任务已经完成时再泛化地询问“现在需要做什么”“是否需要其他任务”。只有当前任务缺少关键信息且无法继续执行时，才通过 AskUser 提问。
4. 只基于本轮用户请求和已实际执行的结果作答；不得把内部运行过程误认为新的用户任务。
""".strip()


class ModelConfig(BaseModel):
    """模型配置"""
    primary: str = Field(default="deepseek-v4-flash", description="主模型")
    fallback: list[str] = Field(default_factory=lambda: ["deepseek-v4-pro"], description="降级模型链")
    temperature: float = Field(default=0.7, ge=0, le=2, description="温度参数")
    top_p: float = Field(default=0.8, ge=0, le=1, description="核采样参数")
    max_tokens: int = Field(default=4096, gt=0, description="最大输出 token 数")
    max_react_iters: int = Field(
        default=40,
        ge=1,
        le=MAX_REACT_ITERS,
        alias="maxReactIters",
        description="ReAct 最大循环次数",
    )

    model_config = {"populate_by_name": True}


class Guardrails(BaseModel):
    """安全边界配置"""
    filesystem_scope: str = Field(default=WORKSPACE_DIR, description="文件系统访问范围")
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
    """历史 Plan 配置兼容字段。

    旧独立 Plan Runtime（runtime.kind=plan）已下线；当前 Plan Mode middleware 不消费该模型。
    """

    enabled: bool = Field(default=False, description="历史兼容字段：旧 runtime.kind=plan 已下线")
    mode: str = Field(default="structured", description="历史兼容字段：旧计划模式")
    max_steps: int = Field(default=6, ge=1, le=20, alias="maxSteps", description="历史兼容字段：旧最大计划步骤数")
    require_plan_before_tools: bool = Field(default=False, alias="requirePlanBeforeTools", description="历史兼容字段：旧工具前计划开关")
    require_user_approval: bool = Field(default=False, alias="requireUserApproval", description="历史兼容字段：旧计划审批开关")
    allow_replanning: bool = Field(default=False, alias="allowReplanning", description="历史兼容字段：旧重规划开关")

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
    plan_config: PlanConfig = Field(default_factory=PlanConfig, alias="planConfig", description="历史兼容字段：旧计划模型配置")
    guardrails: Guardrails = Field(default_factory=Guardrails, description="安全边界配置")
    workspace_id: str = Field(default="default", alias="workspaceId")
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
    plan_config: PlanConfig = Field(default_factory=PlanConfig, alias="planConfig")
    guardrails: Guardrails
    status: str = "idle"
    created_at: str
    updated_at: str
    workspace_id: str = Field(default="default", alias="workspaceId")
    role: str = Field(default="chat")
    job_toolkit: JobToolkit = Field(default_factory=JobToolkit, alias="jobToolkit")

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


def _build_default_agent() -> AgentResponse:
    """构建可持久配置、但不可删除的系统默认 Agent。"""

    runtime_settings = get_effective_model_settings()
    now = datetime.now(timezone.utc).isoformat()
    return AgentResponse.model_validate({
        "id": "default",
        "name": "Assistant",
        "avatar": "",
        "description": "默认助手，可配置模型、工具、Skills 和运行边界",
        "system_prompt": "\n\n".join([
            "你是用户的通用办公助手，负责完成文档、表格、演示文稿、数据分析和文件处理任务。默认使用中文沟通；信息足够时直接执行，只有缺少会改变结果的关键信息时才提问。",
            DEFAULT_AGENT_FINAL_DELIVERY_RULES,
        ]),
        "modelConfig": {
            "primary": runtime_settings.default_model,
            "fallback": runtime_settings.fallback_models,
            "temperature": 0.7,
            "top_p": 0.8,
            "max_tokens": 4096,
            "maxReactIters": 40,
        },
        "mcpTools": [],
        "knowledgeTools": [],
        "skills": [],
        "tags": ["system", "default", DEFAULT_AGENT_PROMPT_VERSION_TAG],
        "planConfig": PlanConfig(),
        "guardrails": Guardrails(),
        "status": "idle",
        "created_at": now,
        "updated_at": now,
        "workspaceId": "default",
        "role": "chat",
        "jobToolkit": {"required": [], "optional": []},
    })


def _ensure_default_agent() -> None:
    """为历史数据库补齐默认 Agent，已有用户配置保持不变。"""

    existing = _agents.get("default")
    if existing is not None:
        _upgrade_default_agent_prompt(existing)
        return
    default_agent = _build_default_agent()
    _agents[default_agent.id] = default_agent
    upsert_agent(default_agent.model_dump(by_alias=True))


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
    _ensure_default_agent()


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
async def list_agents(
    workspace_id: Annotated[str, Query(alias="workspaceId")] = "default",
) -> list[AgentResponse]:
    """
    获取所有 Agent 列表

    Returns:
        list[AgentResponse]: Agent 列表
    """
    return [
        agent
        for agent in _agents.values()
        if agent.id == "default" or agent.workspace_id == workspace_id
    ]


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
        "workspaceId": data.workspace_id,
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
    if "plan_config" in update_dict:
        update_dict["planConfig"] = update_dict.pop("plan_config")
    if "job_toolkit" in update_dict:
        update_dict["jobToolkit"] = update_dict.pop("job_toolkit")

    # 合并更新
    existing_dict = existing.model_dump(by_alias=True)
    existing_dict.update(update_dict)
    existing_dict["updated_at"] = datetime.now(timezone.utc).isoformat()
    if agent_id == "default":
        existing_dict["role"] = CHAT_ROLE
        existing_dict["jobToolkit"] = empty_job_toolkit().model_dump()
    else:
        role, toolkit, guardrails, mcp_tools, knowledge_tools, skills = _apply_job_agent_fields(
            agent_id=agent_id,
            role=str(existing_dict.get("role") or CHAT_ROLE),
            job_toolkit=existing_dict.get("jobToolkit"),
            guardrails=Guardrails.model_validate(existing_dict.get("guardrails") or {}),
            mcp_tools=list(existing_dict.get("mcpTools") or []),
            knowledge_tools=list(existing_dict.get("knowledgeTools") or []),
            skills=list(existing_dict.get("skills") or []),
        )
        existing_dict["role"] = role
        existing_dict["jobToolkit"] = toolkit.model_dump()
        existing_dict["guardrails"] = guardrails.model_dump(by_alias=True)
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
    if agent_id not in _agents:
        raise HTTPException(status_code=404, detail="Agent not found")
    if agent_id == "default":
        raise HTTPException(status_code=403, detail="默认 Agent 不能删除，只能修改配置")
    _job_service.pause_for_resource_change("agent_missing", agent_id=agent_id)
    del _agents[agent_id]
    invalidate_agent(agent_id)
    db_delete_agent(agent_id)
