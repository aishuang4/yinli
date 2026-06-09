"""
Agent 工厂模块 — 从配置创建 AgentScope 2.0 Agent 实例

职责：
1. 根据 Agent 配置创建 DashScope 模型（主模型 + Fallback 链）
2. 注入默认工具集（Read/Write/Edit/Grep/Glob/Bash）
3. 配置 LocalWorkspace 作为工具执行环境
4. 设置 BYPASS 权限模式（工具自动执行，无需确认）
5. 管理 Agent 实例缓存（避免重复创建）
"""

import os
from typing import Optional

from pydantic import SecretStr

from agentscope.agent import Agent
from agentscope.agent._config import ModelConfig
from agentscope.model import DashScopeChatModel
from agentscope.credential import DashScopeCredential
from agentscope.tool import Toolkit, Bash, Read, Write, Edit, Grep, Glob
from agentscope.state import AgentState
from agentscope.permission import PermissionContext
from agentscope.permission._types import PermissionMode
from agentscope.workspace import LocalWorkspace

from src.config.settings import settings


# ============================================================================
# Workspace 目录 — Agent 工具执行的工作空间
# 默认使用用户主目录，让 Agent 能访问真实项目文件
# ============================================================================
WORKSPACE_DIR = os.path.expanduser("~")

# ============================================================================
# Agent 实例缓存 — 按 agent_id 缓存，避免重复创建
# ============================================================================
_agent_cache: dict[str, Agent] = {}


def _create_model(
    model_name: str,
    stream: bool = True,
    thinking_enable: bool = False,
) -> DashScopeChatModel:
    """
    创建 DashScope 聊天模型实例

    Args:
        model_name: 模型名称（如 qwen-plus, qwen-max, qwen-turbo）
        stream: 是否启用流式输出

    Returns:
        DashScopeChatModel: 配置好的模型实例
    """
    return DashScopeChatModel(
        credential=DashScopeCredential(
            api_key=SecretStr(settings.dashscope_api_key),
            base_url=settings.dashscope_base_url,
        ),
        model=model_name,
        parameters=DashScopeChatModel.Parameters(
            thinking_enable=thinking_enable,
        ),
        stream=stream,
        max_retries=3,
    )


def _create_fallback_model(
    fallback_names: list[str],
    thinking_enable: bool = False,
) -> Optional[DashScopeChatModel]:
    """
    创建 Fallback 模型（取第一个作为 fallback_model）

    AgentScope 2.0 的 ModelConfig 只支持单个 fallback_model，
    因此取列表第一个作为 fallback。后续可扩展为链式。

    Args:
        fallback_names: Fallback 模型名称列表（有序）

    Returns:
        最外层 Fallback 模型，如果列表为空则返回 None
    """
    if not fallback_names:
        return None

    # 取第一个 fallback 模型
    return _create_model(fallback_names[0], stream=True, thinking_enable=thinking_enable)


def _create_default_toolkit() -> Toolkit:
    """
    创建默认工具集

    每个 Agent 自动注入以下基础工具：
    - Read: 读取文件
    - Write: 写入文件
    - Edit: 编辑文件
    - Grep: 搜索文件内容
    - Glob: 按模式查找文件
    - Bash: 执行 Shell 命令

    Returns:
        Toolkit: 包含默认工具的 Toolkit 实例
    """
    return Toolkit(
        tools=[
            Read(),
            Write(),
            Edit(),
            Grep(),
            Glob(),
            Bash(),
        ]
    )


def get_or_create_agent(
    agent_id: str = "default",
    name: str = "Assistant",
    system_prompt: str = "You are a helpful assistant.",
    model_name: str | None = None,
    fallback_names: list[str] | None = None,
    thinking_enable: bool = False,
    permission_mode: PermissionMode = PermissionMode.BYPASS,
) -> Agent:
    """
    获取或创建 Agent 实例

    优先从缓存获取，不存在则创建新实例并缓存。

    Args:
        agent_id: Agent 唯一标识（缓存键）
        name: Agent 名称
        system_prompt: 系统提示词
        model_name: 主模型名称（默认使用 settings.default_model）
        fallback_names: Fallback 模型名称列表

    Returns:
        Agent: AgentScope Agent 实例
    """
    # 缓存命中则直接返回
    if agent_id in _agent_cache:
        return _agent_cache[agent_id]

    # 使用配置默认值
    primary_model = model_name or settings.default_model
    fallbacks = fallback_names if fallback_names is not None else settings.fallback_models

    # 创建模型
    main_model = _create_model(primary_model, thinking_enable=thinking_enable)
    fallback_model = _create_fallback_model(fallbacks, thinking_enable=thinking_enable)

    # 创建 LocalWorkspace — Agent 工具执行的隔离环境
    workspace = LocalWorkspace(workdir=WORKSPACE_DIR)

    # 创建 AgentState — 设置 BYPASS 权限模式
    # BYPASS 模式：跳过所有权限检查，工具自动执行无需确认
    agent_state = AgentState(
        permission_context=PermissionContext(
            mode=permission_mode,
        ),
    )

    # 创建 Agent
    agent = Agent(
        name=name,
        system_prompt=system_prompt,
        model=main_model,
        toolkit=_create_default_toolkit(),
        state=agent_state,
        offloader=workspace,
        model_config=ModelConfig(
            max_retries=3,
            fallback_model=fallback_model,
        ),
    )

    # 缓存并返回
    _agent_cache[agent_id] = agent
    return agent


def invalidate_agent(agent_id: str) -> None:
    """
    清除指定 Agent 的缓存

    当 Agent 配置更新时调用，下次请求会重新创建实例。

    Args:
        agent_id: 要清除的 Agent ID
    """
    keys_to_remove = [
        key
        for key in _agent_cache
        if key == agent_id or key.startswith(f"{agent_id}:")
    ]
    for key in keys_to_remove:
        _agent_cache.pop(key, None)


def clear_all_agents() -> None:
    """清除所有 Agent 缓存"""
    _agent_cache.clear()
