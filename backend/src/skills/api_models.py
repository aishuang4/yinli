"""Skill 管理 API 的兼容模型，正文由 reader 显式读取。"""
from typing import Literal, Optional
from pydantic import BaseModel, Field, PrivateAttr

SkillInstallStatus = Literal["available", "installed", "error"]


class Skill(BaseModel):
    """Skill 描述"""

    _resolved_skill: object = PrivateAttr(default=None)
    _catalog_context: object = PrivateAttr(default=None)
    _catalog_snapshot: object = PrivateAttr(default=None)

    id: str
    name: str
    description: str
    category: str
    trigger: str = ""
    content: str = ""
    tags: list[str] = Field(default_factory=list)
    resource_path: str = Field(default="", alias="resourcePath")
    resources: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    status: SkillInstallStatus = "available"
    enabled: bool = False
    available_to_all_agents: bool = Field(default=True, alias="availableToAllAgents")
    installed_at: Optional[str] = Field(default=None, alias="installedAt")
    updated_at: str = Field(alias="updatedAt")
    workspace_id: str = Field(default="default", alias="workspaceId")
    source_kind: str = Field(default="native", alias="sourceKind")
    source_scope: str = Field(default="system", alias="sourceScope")
    source_path: str = Field(default="", alias="sourcePath")
    content_hash: str = Field(default="", alias="contentHash")
    load_name: str = Field(default="", alias="loadName")
    root_key: str = Field(default="", alias="rootKey")
    owner_key: str = Field(default="", alias="ownerKey")
    resolution_state: str = Field(default="resolved", alias="resolutionState")
    error_code: str = Field(default="", alias="errorCode")
    reason: str = ""
    shadowed_by: list[str] = Field(default_factory=list, alias="shadowedBy")
    alternatives: list[str] = Field(default_factory=list)
    managed: bool = False
    catalog_version: str = Field(default="", alias="catalogVersion")

    model_config = {"populate_by_name": True}


class SkillInstallRequest(BaseModel):
    """安装/配置 Skill 请求"""

    enabled: bool = True


class SkillShareRequest(BaseModel):
    """切换「所有 Agent 可用」请求。"""

    available_to_all_agents: bool = Field(alias="availableToAllAgents")

    model_config = {"populate_by_name": True}


class SkillRegisterRequest(BaseModel):
    """注册自定义 Skill 请求"""

    name: str = Field(..., min_length=1, max_length=80)
    description: str = Field(default="", max_length=300)
    category: str = Field(default="自定义", max_length=40)
    trigger: str = Field(default="", max_length=200)
    content: str = Field(default="", max_length=12000)
    tags: list[str] = Field(default_factory=list)
    workspace_id: str = Field(default="default", alias="workspaceId")

    model_config = {"populate_by_name": True}

