"""Chat 单轮运行上下文构建。

本模块只负责把 ChatMessage、Agent 配置、路由结果、Skill 和工具范围
整理成 send.py 可直接执行的上下文，不负责 SSE 输出、持久化和 Agent 执行。
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.config.runtime_settings import get_effective_model_settings
from src.project_files import list_project_session_artifacts, project_session_directory
from src.project_resources import ProjectResourceAccess, resolve_project_resource_access
from src.runtime_context import RuntimeContext, load_project_instructions
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
    scope_guardrails_to_project,
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
    update_skill_run_status,
)
from src.server.routes.skills import Skill, list_skill_usage_details, resolve_runtime_skill_paths


@dataclass
class ChatRunContext:
    """send.py 执行主流程所需的上下文。"""

    session_id: str
    runtime_context: RuntimeContext
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
    project_resources: ProjectResourceAccess | None = None
    skill_paths: list[str] = field(default_factory=list)
    session_workspace_dir: str = ""
    session_artifact_paths_before: list[str] = field(default_factory=list)


async def build_chat_run_context(
    run_data: ChatMessage,
    runtime_context: RuntimeContext,
) -> ChatRunContext:
    """构建单轮 Chat 运行上下文。"""

    session_id = run_data.session_id or ""
    agent_id = run_data.agent_id or "default"
    agent_config = get_registered_agent(agent_id)
    project_resources = resolve_project_resource_access(runtime_context)
    project_resources.require_agent(agent_id)
    if agent_id != "default" and agent_config is None:
        raise ValueError(f"Agent 配置不存在: {agent_id}")
    agent_name = run_data.agent_name or (agent_config.name if agent_config else "Assistant")
    base_system_prompt = (
        run_data.system_prompt
        or (agent_config.system_prompt if agent_config else None)
        or "You are a helpful AI assistant."
    )
    persist_run = not run_data.sandbox

    configured_skill_ids = list(agent_config.skills if agent_config else run_data.skill_ids)
    requested_skill_ids = set(run_data.skill_ids or configured_skill_ids)
    skill_ids = project_resources.filter_ids(
        "skill",
        [skill_id for skill_id in configured_skill_ids if skill_id in requested_skill_ids],
    )
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
        if active_skill and active_skill.id not in project_resources.skill_ids:
            raise ValueError(
                f"Skill {active_skill.id} 已从 Project {runtime_context.project_id} 移除，不能继续当前 Skill Run。",
            )
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
    skill_paths = resolve_runtime_skill_paths(used_skills)
    used_skill_details = list_skill_usage_details(used_skills)
    skill_runs: list[ChatSkillRun] = []
    model_name = runtime_model_config.model_name
    memory_prompt = (
        build_memory_injection_text(
            MemoryRetrievalContext(
                agent_id=agent_id,
                agent_name=agent_name,
                workspace_id=runtime_context.workspace_id,
                project_id=runtime_context.project_id,
            ),
        )
        if readiness.ready
        else ""
    )
    guardrails = scope_guardrails_to_project(
        _build_runtime_guardrails(agent_config),
        runtime_context.project_root,
    )
    session_workspace_dir = str(project_session_directory(
        Path(guardrails.filesystem_scope),
        session_id,
    ))
    session_artifact_paths_before = [
        str(item.get("path") or "")
        for item in list_project_session_artifacts(
            project_root=runtime_context.project_root,
            session_id=session_id,
            session_workspace_dir=session_workspace_dir,
        )
        if str(item.get("path") or "")
    ]
    system_prompt = "\n\n".join(
        part
        for part in [
            base_system_prompt,
            _build_project_context_prompt(runtime_context),
            _build_session_workspace_prompt(session_workspace_dir),
            _build_file_attachments_prompt(run_data.files),
            _build_runtime_context_prompt(),
            intent_route.main_agent_instruction,
            _build_conversation_history_prompt(session_id),
            build_skill_run_system_prompt(active_skill_run),
            memory_prompt,
            _build_clarification_system_prompt(),
            _build_final_answer_system_prompt(),
        ]
        if part
    )

    runtime_tool_scope = resolve_react_runtime_tool_scope(
        message=run_data.message,
        agent_config=agent_config,
        has_selected_skills=bool(used_skills),
        has_team=run_data.mode == "team",
        has_images=bool(run_data.images),
    )
    return ChatRunContext(
        session_id=session_id,
        runtime_context=runtime_context,
        project_resources=project_resources,
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
        skill_paths=skill_paths,
        used_skill_details=used_skill_details,
        skill_runs=skill_runs,
        active_skill_run=active_skill_run,
        model_name=model_name,
        system_prompt=system_prompt,
        guardrails=guardrails,
        runtime_tool_scope=runtime_tool_scope,
        mcp_servers=_build_runtime_mcp_servers(
            agent_config,
            allowed_server_ids=project_resources.mcp_server_ids,
        ),
        knowledge_tools=_build_runtime_knowledge_tools(
            agent_config,
            allowed_tool_ids=project_resources.http_tool_ids,
        ),
        session_workspace_dir=session_workspace_dir,
        session_artifact_paths_before=session_artifact_paths_before,
    )


def _build_project_context_prompt(runtime_context: RuntimeContext) -> str:
    """构造 Project root 和项目说明提示。"""

    instructions = load_project_instructions(runtime_context)
    parts = [
        "# Project Context",
        f"Workspace ID: {runtime_context.workspace_id}",
        f"Project ID: {runtime_context.project_id}",
        f"Project name: {runtime_context.project_name}",
        f"Project root: {runtime_context.project_root}",
        "All file operations must remain within the effective project filesystem scope.",
    ]
    if instructions:
        parts.extend(["", "## Project Instructions", instructions])
    return "\n".join(parts)


def _build_file_attachments_prompt(files) -> str:
    """声明本轮普通附件在 Project 内的位置，避免模型自行猜测路径。"""

    if not files:
        return ""
    lines = [
        "## Attached Files",
        "The user uploaded these files to the current Project. Read or modify them using the listed relative paths.",
    ]
    lines.extend(
        f"- {item.name}: `{item.path}` ({item.size} bytes, {item.media_type})"
        for item in files
    )
    return "\n".join(lines)


def _build_session_workspace_prompt(session_workspace_dir: str) -> str:
    """声明本 Session 的默认产物目录。"""

    return "\n".join([
        "## Session Workspace",
        f"Default working directory for this session: {session_workspace_dir}",
        "Create every new deliverable with a relative path so it stays in this directory.",
        "Use an explicit Project path only when the user specifically asks to modify an existing Project file.",
    ])
