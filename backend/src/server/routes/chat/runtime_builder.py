"""Chat Runtime 组装辅助。

这里承接前置 IntentRoute 之后的纯编排逻辑，避免 send.py 同时负责
路由、运行时配置、缓存 key 和 SSE 事件处理。主 Agent 规格已下沉到
src.runtime.agent_spec，本模块继续 re-export，旧调用点不必一次改完。
"""

from typing import Any

from src.agents.factory import AgentModelOptions, RuntimeToolScope
from src.runtime.agent_spec import (
    MainAgentRuntimeSpec,
    RuntimeModelConfig,
    build_main_agent_runtime_spec,
    build_runtime_agent_cache_key,
    create_main_agent_from_spec,
)
from src.server.routes.chat.models import ChatMessage
from src.server.routes.chat.tool_scope_resolver import resolve_runtime_tool_scope
from src.server.routes.skills import Skill, get_installed_skill

__all__ = [
    "MainAgentRuntimeSpec",
    "RuntimeModelConfig",
    "build_main_agent_runtime_spec",
    "build_runtime_agent_cache_key",
    "build_runtime_model_config",
    "build_runtime_skills",
    "create_main_agent_from_spec",
    "resolve_react_runtime_tool_scope",
]


def build_runtime_model_config(
    *,
    run_data: ChatMessage,
    agent_config: object | None,
    runtime_model_settings: Any,
) -> RuntimeModelConfig:
    """合成本轮实际使用的主模型、fallback 和采样参数。"""

    model_config_data = agent_config.model_config_data if agent_config else None
    model_name = runtime_model_settings.default_model
    fallback_names = runtime_model_settings.fallback_models
    return RuntimeModelConfig(
        model_name=model_name,
        fallback_names=fallback_names,
        model_options=AgentModelOptions(
            max_react_iters=int(getattr(model_config_data, "max_react_iters", 128) or 128) if model_config_data else 128,
        ),
    )


def build_runtime_skills(
    *,
    skill_ids: list[str],
    message: str,
    active_skill: Skill | None,
    external_skills: list[Skill] | None = None,
) -> list[Skill]:
    """选择本轮 Skill；正在恢复的 Skill 优先。"""

    del message
    external_by_ref = {
        ref: skill
        for skill in (external_skills or [])
        for ref in (skill.id, skill.name)
    }
    available = [
        skill
        for skill_id in skill_ids
        if (skill := get_installed_skill(skill_id) or external_by_ref.get(skill_id)) is not None
    ]
    if active_skill and all(skill.id != active_skill.id for skill in available):
        available.insert(0, active_skill)
    return available


def resolve_react_runtime_tool_scope(
    *,
    message: str,
    agent_config: object | None,
    has_selected_skills: bool,
    has_team: bool,
    has_images: bool,
) -> RuntimeToolScope:
    """解析 ReAct Runtime 使用的工具范围。"""

    return resolve_runtime_tool_scope(
        message=message,
        agent_config=agent_config,
        has_selected_skills=has_selected_skills,
        has_team=has_team,
        has_images=has_images,
    ).scope
