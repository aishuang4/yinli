"""持久 Agent 的 Pydantic 模型。供 catalog 与 HTTP 路由共用，避免 catalog import routes。"""

from __future__ import annotations

from pydantic import BaseModel, Field, computed_field, field_validator, model_validator

from src.agents.runtime_types import MAX_REACT_ITERS
from src.agents.system_agents import is_system_agent_id
from src.scheduler.job_toolkit import JobToolkit


class ModelConfig(BaseModel):
    """Agent 执行上限；历史模型字段在导入时忽略。"""

    max_react_iters: int = Field(
        default=128,
        ge=1,
        le=MAX_REACT_ITERS,
        alias="maxReactIters",
        description="一次提问的 ReAct 轮次预算。死循环由工具护栏单独停止。",
    )

    model_config = {"populate_by_name": True}


class Guardrails(BaseModel):
    """安全边界配置。"""

    filesystem_scope: str = Field(default="", description="文件系统访问范围；空表示默认 Project 根")
    shell_blacklist: list[str] = Field(default_factory=lambda: ["rm -rf /", "sudo"], description="Shell 命令黑名单")
    network_enabled: bool = Field(default=True, description="是否允许网络访问")
    operation_scope: str = Field(
        default="normal",
        alias="operationScope",
        description="能力范围：normal 或 read_only。不决定工具要不要审批。",
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

    model_config = {"populate_by_name": True, "extra": "ignore"}

    @model_validator(mode="before")
    @classmethod
    def _lift_legacy_read_only(cls, data: object) -> object:
        """旧工具权限档里的 read_only 收成能力范围，审批档丢弃。"""

        if not isinstance(data, dict):
            return data
        payload = dict(data)
        profile = payload.get("tool_permission_profile", payload.get("toolPermissionProfile"))
        if (
            str(profile or "") == "read_only"
            and not payload.get("operation_scope")
            and not payload.get("operationScope")
        ):
            payload["operation_scope"] = "read_only"
        return payload

    @field_validator("operation_scope", mode="before")
    @classmethod
    def _normalize_operation_scope(cls, value: object) -> str:
        return "read_only" if str(value or "") == "read_only" else "normal"


class PlanConfig(BaseModel):
    """历史 Plan 配置兼容字段。当前 Plan Mode middleware 不消费。"""

    enabled: bool = Field(default=False, description="历史兼容字段：旧 runtime.kind=plan 已下线")
    mode: str = Field(default="structured", description="历史兼容字段：旧计划模式")
    max_steps: int = Field(default=6, ge=1, le=20, alias="maxSteps", description="历史兼容字段：旧最大计划步骤数")
    require_plan_before_tools: bool = Field(default=False, alias="requirePlanBeforeTools", description="历史兼容字段：旧工具前计划开关")
    require_user_approval: bool = Field(default=False, alias="requireUserApproval", description="历史兼容字段：旧计划审批开关")
    allow_replanning: bool = Field(default=False, alias="allowReplanning", description="历史兼容字段：旧重规划开关")

    model_config = {"populate_by_name": True}


class AgentResponse(BaseModel):
    """Agent 响应体 / catalog 记录。"""

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
    role: str = Field(default="chat")
    job_toolkit: JobToolkit = Field(default_factory=JobToolkit, alias="jobToolkit")
    source_path: str = Field(default="", alias="sourcePath")
    source_kind: str = Field(default="profile", alias="sourceKind")

    @computed_field(alias="systemManaged")
    @property
    def system_managed(self) -> bool:
        """系统身份由稳定 ID 派生。"""

        return is_system_agent_id(self.id)

    model_config = {"populate_by_name": True, "extra": "ignore"}


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
