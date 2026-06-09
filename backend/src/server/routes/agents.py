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
    max_tokens: int = Field(default=4096, gt=0, description="最大输出 token 数")


class Guardrails(BaseModel):
    """安全边界配置"""
    filesystem_scope: str = Field(default="/", description="文件系统访问范围")
    shell_blacklist: list[str] = Field(default_factory=lambda: ["rm -rf /", "sudo"], description="Shell 命令黑名单")
    network_enabled: bool = Field(default=True, description="是否允许网络访问")
    human_approval: bool = Field(default=False, description="是否启用人工审批")
    timeout_seconds: int = Field(default=300, gt=0, description="工具调用超时（秒）")


class AgentCreate(BaseModel):
    """创建 Agent 的请求体"""
    name: str = Field(..., min_length=1, max_length=50, description="Agent 名称")
    avatar: str = Field(default="", description="头像 URL")
    description: str = Field(default="", max_length=200, description="一句话描述")
    system_prompt: str = Field(default="You are a helpful assistant.", description="系统提示词")
    model_config_data: ModelConfig = Field(default_factory=ModelConfig, alias="modelConfig", description="模型配置")
    mcp_tools: list[str] = Field(default_factory=list, alias="mcpTools", description="MCP 工具 ID 列表")
    skills: list[str] = Field(default_factory=list, description="Skills ID 列表")
    tags: list[str] = Field(default_factory=list, description="标签")
    callable_agents: list[str] = Field(default_factory=list, alias="callableAgents", description="可调用的子 Agent ID 列表")
    max_depth: int = Field(default=2, ge=1, le=5, alias="maxDepth", description="最大调用深度")
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
    skills: Optional[list[str]] = None
    tags: Optional[list[str]] = None
    callable_agents: Optional[list[str]] = Field(None, alias="callableAgents")
    max_depth: Optional[int] = Field(None, ge=1, le=5, alias="maxDepth")
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
    skills: list[str]
    tags: list[str]
    callable_agents: list[str] = Field(alias="callableAgents")
    max_depth: int = Field(alias="maxDepth")
    guardrails: Guardrails
    status: str = "idle"
    created_at: str
    updated_at: str

    model_config = {"populate_by_name": True}


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
    return {
        "primary": [
            {"id": "qwen3.7-max", "name": "Qwen 3.7 Max", "description": "深度思考，非多模态模型"},
            {"id": "qwen3.7-plus", "name": "Qwen 3.7 Plus", "description": "深度思考，多模态图片输入"},
            {"id": "qwen-max", "name": "Qwen Max", "description": "最强性能，复杂任务"},
            {"id": "qwen-plus", "name": "Qwen Plus", "description": "均衡性能，日常任务"},
            {"id": "qwen-turbo", "name": "Qwen Turbo", "description": "快速响应，简单任务"},
        ],
        "fallback": [
            {"id": "qwen3.7-max", "name": "Qwen 3.7 Max", "description": "强能力降级选项，非多模态"},
            {"id": "qwen3.7-plus", "name": "Qwen 3.7 Plus", "description": "多模态降级选项"},
            {"id": "qwen-max", "name": "Qwen Max", "description": "高质量降级选项"},
            {"id": "qwen-plus", "name": "Qwen Plus", "description": "均衡降级选项"},
            {"id": "qwen-turbo", "name": "Qwen Turbo", "description": "快速降级选项"},
            {"id": "qwen-long", "name": "Qwen Long", "description": "长文本降级选项"},
        ],
    }


@router.get("", response_model=list[AgentResponse])
async def list_agents() -> list[AgentResponse]:
    """
    获取所有 Agent 列表

    Returns:
        list[AgentResponse]: Agent 列表
    """
    return list(_agents.values())


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
        "skills": data.skills,
        "tags": data.tags,
        "callableAgents": data.callable_agents,
        "maxDepth": data.max_depth,
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
    if "callable_agents" in update_dict:
        update_dict["callableAgents"] = update_dict.pop("callable_agents")
    if "max_depth" in update_dict:
        update_dict["maxDepth"] = update_dict.pop("max_depth")

    # 合并更新
    existing_dict = existing.model_dump(by_alias=True)
    existing_dict.update(update_dict)
    existing_dict["updated_at"] = datetime.now(timezone.utc).isoformat()

    updated_agent = AgentResponse(**existing_dict)
    _agents[agent_id] = updated_agent
    invalidate_agent(agent_id)
    upsert_agent(updated_agent.model_dump(by_alias=True))
    return updated_agent


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
