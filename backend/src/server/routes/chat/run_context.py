"""Chat 单轮运行上下文构建。

本模块只负责把 ChatMessage、Agent 配置、路由结果、Skill 和工具范围
整理成 send.py 可直接执行的上下文，不负责 SSE 输出、持久化和 Agent 执行。
"""

from dataclasses import dataclass
from typing import Any

from src.config.runtime_settings import get_effective_model_settings
from src.memory.retrieval import MemoryRetrievalContext, build_memory_injection_text
from src.server.routes.agents import get_registered_agent
from src.server.routes.chat.intent_router import IntentRoute, route_chat_message, route_chat_message_with_model
from src.server.routes.chat.models import ChatMessage, ChatSkillRun
from src.server.routes.chat.prompts import (
    _build_clarification_system_prompt,
    _build_conversation_history_prompt,
    _build_final_answer_system_prompt,
    _build_runtime_context_prompt,
)
from src.server.routes.chat.runtime import (
    _build_runtime_guardrails,
    _build_runtime_knowledge_tools,
    _build_runtime_mcp_servers,
)
from src.server.routes.chat.readiness_gate import ReadinessDecision, check_message_readiness_with_model
from src.server.routes.chat.runtime_builder import (
    RuntimeModelConfig,
    build_runtime_skills,
    build_runtime_model_config,
    resolve_react_runtime_tool_scope,
)
from src.server.routes.chat.skill_runs import (
    build_skill_run_system_prompt,
    get_active_session_skill_run,
    get_skill_for_run,
    start_or_resume_skill_run,
    update_skill_run_status,
)
from src.server.routes.skills import Skill, build_skill_system_prompt_from_skills, list_skill_usage_details


@dataclass
class ChatRunContext:
    """send.py 执行主流程所需的上下文。"""

    session_id: str
    agent_id: str
    agent_name: str
    agent_config: Any | None
    persist_run: bool
    runtime_model_settings: Any
    runtime_model_config: RuntimeModelConfig
    readiness: ReadinessDecision
    intent_route: IntentRoute
    effective_user_message: str
    used_skills: list[Skill]
    used_skill_details: list[dict]
    skill_runs: list[ChatSkillRun]
    active_skill_run: dict | None
    model_name: str
    system_prompt: str
    guardrails: Any
    runtime_tool_scope: Any
    mcp_servers: list[Any]
    knowledge_tools: list[Any]


async def build_chat_run_context(run_data: ChatMessage) -> ChatRunContext:
    """构建单轮 Chat 运行上下文。"""

    session_id = run_data.session_id or ""
    agent_id = run_data.agent_id or "default"
    agent_config = get_registered_agent(agent_id)
    agent_name = run_data.agent_name or (agent_config.name if agent_config else "Assistant")
    base_system_prompt = (
        run_data.system_prompt
        or (agent_config.system_prompt if agent_config else None)
        or "You are a helpful AI assistant."
    )
    persist_run = not run_data.sandbox

    skill_ids = agent_config.skills if agent_config and not run_data.skill_ids else run_data.skill_ids
    runtime_model_settings = get_effective_model_settings()
    runtime_model_config = build_runtime_model_config(
        run_data=run_data,
        agent_config=agent_config,
        runtime_model_settings=runtime_model_settings,
    )
    readiness = await check_message_readiness_with_model(
        message=run_data.message,
        agent_config=agent_config,
        skill_ids=skill_ids,
        execution_mode=run_data.mode,
        images=run_data.images,
        model_name=runtime_model_config.model_name,
        fallback_names=runtime_model_config.fallback_names,
    )
    if readiness.ready:
        intent_route = await route_chat_message_with_model(
            message=run_data.message,
            agent_config=agent_config,
            execution_mode=run_data.mode,
            images=run_data.images,
            model_name=runtime_model_config.model_name,
            fallback_names=runtime_model_config.fallback_names,
        )
    else:
        intent_route = route_chat_message(execution_mode=run_data.mode)

    active_skill_run = None
    used_skills: list[Skill] = []
    if readiness.ready:
        active_skill_run = get_active_session_skill_run(session_id, agent_id)
        active_skill = get_skill_for_run(active_skill_run)
        if active_skill_run and active_skill_run.get("status") == "waiting_user" and persist_run:
            active_skill_run = update_skill_run_status(
                active_skill_run,
                status="active",
                current_step="resumed_with_user_input",
                pending_interaction_id="",
            )
        used_skills = build_runtime_skills(
            skill_ids=skill_ids,
            message=run_data.message,
            active_skill=active_skill,
        )
        if not active_skill_run and used_skills and persist_run:
            active_skill_run = start_or_resume_skill_run(
                session_id=session_id,
                agent_id=agent_id,
                skill=used_skills[0],
                status="active",
                current_step="started",
            )

    used_skill_details = list_skill_usage_details(used_skills)
    skill_runs = [
        ChatSkillRun.model_validate({**skill_detail, "status": "running"})
        for skill_detail in used_skill_details
    ]
    model_name = runtime_model_config.model_name
    memory_prompt = (
        build_memory_injection_text(
            MemoryRetrievalContext(
                agent_id=agent_id,
                agent_name=agent_name,
            ),
        )
        if readiness.ready
        else ""
    )
    system_prompt = "\n\n".join(
        part
        for part in [
            base_system_prompt,
            _build_runtime_context_prompt(),
            intent_route.main_agent_instruction,
            _build_conversation_history_prompt(session_id),
            build_skill_run_system_prompt(active_skill_run),
            build_skill_system_prompt_from_skills(used_skills),
            memory_prompt,
            _build_clarification_system_prompt(),
            _build_final_answer_system_prompt(),
        ]
        if part
    )

    guardrails = _build_runtime_guardrails(agent_config)
    runtime_tool_scope = resolve_react_runtime_tool_scope(
        message=run_data.message,
        agent_config=agent_config,
        has_selected_skills=bool(used_skills),
        has_team=run_data.mode == "team",
        has_images=bool(run_data.images),
    )
    return ChatRunContext(
        session_id=session_id,
        agent_id=agent_id,
        agent_name=agent_name,
        agent_config=agent_config,
        persist_run=persist_run,
        runtime_model_settings=runtime_model_settings,
        runtime_model_config=runtime_model_config,
        readiness=readiness,
        intent_route=intent_route,
        effective_user_message=run_data.message,
        used_skills=used_skills,
        used_skill_details=used_skill_details,
        skill_runs=skill_runs,
        active_skill_run=active_skill_run,
        model_name=model_name,
        system_prompt=system_prompt,
        guardrails=guardrails,
        runtime_tool_scope=runtime_tool_scope,
        mcp_servers=_build_runtime_mcp_servers(agent_config),
        knowledge_tools=_build_runtime_knowledge_tools(agent_config),
    )
