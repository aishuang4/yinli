"""Chat 单轮运行上下文构建。

本模块只负责把 ChatMessage、Agent 配置、路由结果、Skill 和工具范围
整理成 send.py 可直接执行的上下文，不负责 SSE 输出、持久化和 Agent 执行。
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from src.config.runtime_settings import get_effective_model_settings
from src.project_files import list_project_session_artifacts, project_session_directory
from src.project_resources import ProjectResourceAccess, load_project_access
from src.runtime_context import RuntimeContext, load_project_instructions
from src.server.routes.chat.runtime_governance import build_memory_prompt_and_recall
from src.server.routes.agents import get_registered_agent
from src.server.routes.chat.intent_router import (
    ExecutorRoute,
    IntentRoute,
    RuntimeRoute,
    route_chat_message,
    route_chat_message_with_model,
)
from src.server.routes.chat.models import ChatMessage, ChatSkillRun
from src.agents.session_context import SessionHistoryMessage
from src.server.routes.chat.prompts import (
    _build_clarification_system_prompt,
    _build_file_attachments_user_note,
    _build_final_answer_system_prompt,
    _build_forced_skills_user_note,
    _build_runtime_context_prompt,
    _build_skill_context_prompt,
)
from src.server.routes.chat.store import message_model
from src.storage.database import list_chat_messages
from src.server.routes.chat.runtime import (
    _build_runtime_guardrails,
    _build_runtime_knowledge_tools,
    _build_runtime_mcp_servers,
    directory_scope_for_run,
)
from src.server.routes.chat.readiness_gate import ReadinessDecision, check_message_readiness_with_model
from src.server.routes.chat.runtime_builder import (
    RuntimeModelConfig,
    build_runtime_skills,
    build_runtime_model_config,
    resolve_react_runtime_tool_scope,
)
from src.server.routes.chat.scheduled_runtime import (
    SCHEDULED_JOB_UNATTENDED_DENYLIST,
    deserialize_frozen_runtime_tool_scope,
)
from src.server.routes.chat.skill_runs import (
    build_skill_run_system_prompt,
    get_active_session_skill_run,
    get_skill_for_run,
    update_skill_run_status,
)
from src.server.routes.skills import (
    Skill,
    discover_external_skills,
    get_installed_skill,
    list_skill_usage_details,
    resolve_runtime_skill_paths,
)
from src.storage.database import get_project


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
    trigger_source: str = "user"
    approval_mode: str = "manual"
    tool_name_denylist: set[str] = field(default_factory=set)
    write_scope_dir: str = ""
    scheduled_job_id: str = ""
    scheduled_firing_id: str = ""
    extra_filesystem_scopes: list[str] = field(default_factory=list)
    # 本轮开始前的会话正文快照（不含本轮用户消息），只在进程内没有
    # AgentScope 上下文时用于重建，不再拼进 system prompt。
    session_history: list[SessionHistoryMessage] = field(default_factory=list)
    # 本轮附件说明，作为用户消息的一部分进入对话上下文，而不是 system prompt。
    user_message_note: str = ""
    # system prompt 各段字符数（段名 -> chars），只用于 contextStats / CONTEXT_USAGE 定位膨胀来源。
    system_prompt_sections: dict[str, int] = field(default_factory=dict)
    # 记忆注入的两段：Markdown 摘要（已在 system_prompt 中）与本轮召回（已在 user_message_note 中）；
    # 单独保留是给 Team Leader / 综合阶段复用，不必再算一次。
    memory_prompt: str = ""
    memory_recall_text: str = ""
    # 本轮用户在发送框强制开启的 Skill；已并入 used_skills，单独保留供预启用。
    forced_skills: list[Skill] = field(default_factory=list)
    force_compress: bool = False
    platform_binding: object | None = None
    platform_denied: frozenset[str] = field(default_factory=frozenset)
    platform_filesystem: str = ""


def snapshot_session_history(session_id: str | None, *, limit: int = 40) -> list[SessionHistoryMessage]:
    """读取会话已持久化的 user / assistant 正文，供 AgentScope 上下文重建。"""

    if not session_id:
        return []
    messages = [message_model(item) for item in list_chat_messages(session_id)]
    return [
        SessionHistoryMessage(
            role="user" if message.role == "user" else "assistant",
            content=message.content or "",
            file_names=tuple(item.name for item in message.files),
        )
        for message in messages[-limit:]
        if message.role in {"user", "assistant"} and not message.error
    ]


def resolve_agent_system_prompt(agent_config: Any, run_data: ChatMessage) -> str:
    """持久 Agent 提示词以 catalog 文件为准。

    Chat 前端会把列表里缓存的 systemPrompt 随请求带上；文件被 Edit 后
    这份缓存仍是旧值。有注册 Agent 时忽略请求覆盖。
    """

    catalog_prompt = str(getattr(agent_config, "system_prompt", "") or "").strip()
    if catalog_prompt:
        return catalog_prompt
    request_prompt = str(getattr(run_data, "system_prompt", None) or "").strip()
    if request_prompt:
        return request_prompt
    return "You are a helpful AI assistant."


def resolve_run_workspace_dir(
    *,
    filesystem_scope: str | Path,
    session_id: str,
    session_workspace_dir: str | None = None,
) -> str:
    """解析本轮 cwd。传入覆盖路径时不创建 sessions/{session_id}。"""

    if session_workspace_dir:
        directory = Path(session_workspace_dir).expanduser().resolve()
        directory.mkdir(parents=True, exist_ok=True)
        return str(directory)
    return str(project_session_directory(Path(filesystem_scope), session_id))


def approval_mode_for_run(session_id: str, requested: str | None) -> str:
    """请求里显式给出的模式优先；否则读会话，缺失时保持人工。"""

    if requested in {"manual", "model_review", "full_access"}:
        return requested
    return _session_approval_mode(session_id)


def _session_approval_mode(session_id: str) -> str:
    """下一轮模式来自会话；缺失时保持人工，不改写成完全开放。"""

    from src.runtime.chat_session import find_chat_session

    session = find_chat_session(session_id) if session_id else None
    mode = str(getattr(session, "approval_mode", "") or "manual")
    return mode if mode in {"manual", "model_review", "full_access"} else "manual"


def _job_approval_mode(job: object) -> str:
    """Job 的审批模式只来自执行策略；没有策略时按人工处理。"""

    import json

    raw = getattr(job, "execution_policy", None)
    if raw is None:
        raw = getattr(job, "executionPolicy", None)
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "{}")
        except json.JSONDecodeError:
            raw = {}
    mode = str((raw or {}).get("approvalMode") or "manual") if isinstance(raw, dict) else "manual"
    return mode if mode in {"manual", "model_review", "full_access"} else "manual"


def _require_job_approval_mode(job: object) -> str:
    """model_review 不可用时直接失败，不把 Job 改成完全开放。"""

    mode = _job_approval_mode(job)
    if mode == "model_review":
        from src.security.authorization.review_gate import permission_review_status

        review = permission_review_status()
        if not review["available"]:
            raise ValueError(review["reason"] or "智能审核当前不可用，本次定时运行已结束。")
    return mode


async def build_run_context(
    run_data: ChatMessage,
    runtime_context: RuntimeContext,
    *,
    mode: Literal["interactive", "frozen"] = "interactive",
    job: Any | None = None,
    firing: Any | None = None,
    session_workspace_dir: str | None = None,
) -> ChatRunContext:
    """交互式與定時 firing 共用入口。frozen 只讀確認時寫下的 Skill 記錄。"""

    if mode == "frozen":
        if job is None or firing is None:
            raise ValueError("frozen 模式需要 job 與 firing")
        return await build_scheduled_run_context(
            job,
            firing,
            run_data=run_data,
            runtime_context=runtime_context,
            session_workspace_dir=session_workspace_dir,
        )
    return await build_chat_run_context(
        run_data,
        runtime_context,
        session_workspace_dir=session_workspace_dir,
    )


async def build_chat_run_context(
    run_data: ChatMessage,
    runtime_context: RuntimeContext,
    *,
    session_workspace_dir: str | None = None,
) -> ChatRunContext:
    """构建单轮 Chat 运行上下文。"""

    session_id = run_data.session_id or ""
    agent_id = run_data.agent_id or "default"
    agent_config = get_registered_agent(agent_id)
    project_resources = load_project_access(runtime_context)
    project_resources.require_agent(agent_id)
    if agent_id != "default" and agent_config is None:
        raise ValueError(f"Agent 配置不存在: {agent_id}")
    agent_name = run_data.agent_name or (agent_config.name if agent_config else "Assistant")
    base_system_prompt = resolve_agent_system_prompt(agent_config, run_data)
    persist_run = not run_data.sandbox

    external_skills = discover_external_skills(runtime_context.project_root)
    configured_skill_ids = list(agent_config.skills if agent_config else run_data.skill_ids)
    requested_skill_ids = set(run_data.skill_ids or configured_skill_ids)
    skill_ids = _enabled_scanned_skill_ids(
        [skill_id for skill_id in configured_skill_ids if skill_id in requested_skill_ids],
        external_skills,
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
            external_skills=external_skills,
        )
    used_skills = _merge_file_catalog_skills(
        used_skills,
        external_skills=external_skills,
        inject=True,
        configured_skill_ids=configured_skill_ids,
    )
    forced_skills = resolve_forced_runtime_skills(
        list(run_data.forced_skill_ids or []),
        project_resources=project_resources,
        external_skills=external_skills,
    )
    used_skills = _merge_forced_skills(used_skills, forced_skills)
    from src.runtime.capabilities import resolve_capabilities

    used_skills = list(resolve_capabilities(
        mode="interactive",
        agent=agent_config,
        requested_skill_ids=[str(getattr(skill, "id", "") or "") for skill in forced_skills],
        candidates=used_skills,
    ).skills)
    skill_paths = resolve_runtime_skill_paths(used_skills, runtime_context=runtime_context)
    used_skill_details = list_skill_usage_details(used_skills)
    skill_runs: list[ChatSkillRun] = []
    model_name = runtime_model_config.model_name
    # 记忆摘要按会话冻结：每轮后台新增的历史记忆节点不再改写 system prompt，
    # 供应商前缀 cache 才能跨轮命中（见 memory_prompt_cache.py）。
    # 记忆摘要按会话冻结（供应商前缀 cache），历史记忆按本轮消息召回并附在用户消息后（见 runtime_governance.py）。
    memory_prompt, memory_recall = ("", None)
    if readiness.ready:
        memory_prompt, memory_recall = build_memory_prompt_and_recall(
            agent_id=agent_id,
            agent_name=agent_name,
            project_slug=runtime_context.project_slug,
            query_text=run_data.message,
            freeze_session_id=session_id if persist_run else None,
            exclude_session_id=session_id or "",
        )
        del memory_recall
    guardrails, readable_scopes = directory_scope_for_run(
        _build_runtime_guardrails(agent_config),
        runtime_context,
    )
    workspace_override = bool(session_workspace_dir)
    session_workspace_dir = resolve_run_workspace_dir(
        filesystem_scope=guardrails.filesystem_scope,
        session_id=session_id,
        session_workspace_dir=session_workspace_dir,
    )
    session_artifact_paths_before = [
        str(item.get("path") or "")
        for item in list_project_session_artifacts(
            project_root=runtime_context.project_root,
            session_id=session_id,
            session_workspace_dir=session_workspace_dir,
        )
        if str(item.get("path") or "")
    ]
    # 会话历史不再扁平化进 system prompt：AgentScope `state.context` 是在线记忆，
    # 这里只留一份快照供进程重启后重建。
    session_history = snapshot_session_history(session_id) if persist_run else []
    prompt_sections: list[tuple[str, str]] = [
        ("agent", base_system_prompt),
        ("project", _build_project_context_prompt(runtime_context)),
        (
            "workspace",
            _build_job_workspace_prompt(session_workspace_dir)
            if workspace_override
            else _build_session_workspace_prompt(session_workspace_dir),
        ),
        ("runtime", _build_runtime_context_prompt()),
        ("skills", _build_skill_context_prompt() if used_skills else ""),
        ("route", intent_route.main_agent_instruction),
        ("skillRun", build_skill_run_system_prompt(active_skill_run)),
        ("memory", memory_prompt),
        ("clarification", _build_clarification_system_prompt()),
        ("finalAnswer", _build_final_answer_system_prompt()),
    ]
    system_prompt = "\n\n".join(part for _, part in prompt_sections if part)
    system_prompt_sections = {name: len(part) for name, part in prompt_sections if part}

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
        forced_skills=forced_skills,
        skill_paths=skill_paths,
        used_skill_details=used_skill_details,
        skill_runs=skill_runs,
        active_skill_run=active_skill_run,
        model_name=model_name,
        system_prompt=system_prompt,
        guardrails=guardrails,
        runtime_tool_scope=runtime_tool_scope,
        mcp_servers=_build_runtime_mcp_servers(agent_config),
        knowledge_tools=_build_runtime_knowledge_tools(agent_config),
        session_workspace_dir=session_workspace_dir,
        session_artifact_paths_before=session_artifact_paths_before,
        approval_mode=approval_mode_for_run(session_id, getattr(run_data, "approval_mode", None)),
        extra_filesystem_scopes=readable_scopes,
        session_history=session_history,
        user_message_note="\n\n".join(
            part
            for part in [
                _build_file_attachments_user_note(run_data.files),
                _build_forced_skills_user_note(forced_skills),
            ]
            if part
        ),
        system_prompt_sections=system_prompt_sections,
        memory_prompt=memory_prompt,
        memory_recall_text="",
        force_compress=bool(getattr(run_data, "force_compress", False)),
    )


async def build_scheduled_run_context(
    job: Any,
    firing: Any,
    *,
    run_data: ChatMessage,
    runtime_context: RuntimeContext,
    session_workspace_dir: str,
) -> ChatRunContext:
    """组装无人值守 firing 的 ChatRunContext。

    不调用模型 Readiness / intent，不跑 tool_scope_resolver。
    只认 required_skills 冻结记录；校验失败不回退 skill_ids。
    """

    frozen_prompt = str(getattr(job, "frozen_prompt", "") or run_data.message or "").strip()
    if not frozen_prompt:
        raise ValueError("frozen_prompt 不能为空")
    project = get_project(runtime_context.project_slug)
    if project is None:
        raise ValueError(f"project_archived: 找不到 Project {runtime_context.project_slug}")
    if str(project.get("status") or "") != "active":
        raise ValueError(f"project_archived: Project {runtime_context.project_slug} 已归档")

    session_id = run_data.session_id or ""
    agent_id = str(getattr(job, "agent_id", "") or run_data.agent_id or "default")
    agent_config = get_registered_agent(agent_id)
    if agent_id != "default" and agent_config is None:
        raise ValueError(f"agent_missing: Agent 配置不存在: {agent_id}")
    project_resources = load_project_access(runtime_context)
    project_resources.require_agent(agent_id)
    agent_name = run_data.agent_name or (agent_config.name if agent_config else "Assistant")
    base_system_prompt = resolve_agent_system_prompt(agent_config, run_data)
    frozen_records = list(getattr(job, "required_skills", None) or [])
    from src.runtime.capabilities import SkillManifestError, resolve_capabilities

    try:
        used_skills = list(resolve_capabilities(
            mode="frozen",
            agent=agent_config,
            frozen=frozen_records,
        ).skills)
    except SkillManifestError as exc:
        raise ValueError(str(exc)) from exc
    runtime_model_settings = get_effective_model_settings()
    runtime_model_config = build_runtime_model_config(
        run_data=run_data,
        agent_config=agent_config,
        runtime_model_settings=runtime_model_settings,
    )
    readiness = ReadinessDecision(
        ready=True,
        source="scheduled_job",
        reason="frozen scheduled job spec",
    )
    intent_route = IntentRoute(
        mode="react",
        runtime=RuntimeRoute(kind="react", reason="scheduled_job"),
        executor=ExecutorRoute(kind="single_agent", reason="scheduled_job"),
        main_agent_instruction="",
    )
    skill_paths = resolve_runtime_skill_paths(used_skills, runtime_context=runtime_context)
    used_skill_details = list_skill_usage_details(used_skills)
    # 定时任务每次都是新会话：摘要不冻结，按任务正文召回历史记忆。
    memory_prompt, _job_recall = build_memory_prompt_and_recall(
        agent_id=agent_id,
        agent_name=agent_name,
        project_slug=runtime_context.project_slug,
        query_text=run_data.message,
        freeze_session_id=None,
    )
    guardrails, readable_scopes = directory_scope_for_run(
        _build_runtime_guardrails(agent_config),
        runtime_context,
    )
    workspace_dir = resolve_run_workspace_dir(
        filesystem_scope=guardrails.filesystem_scope,
        session_id=session_id,
        session_workspace_dir=session_workspace_dir,
    )
    session_artifact_paths_before = [
        str(item.get("path") or "")
        for item in list_project_session_artifacts(
            project_root=runtime_context.project_root,
            session_id=session_id,
            session_workspace_dir=workspace_dir,
        )
        if str(item.get("path") or "")
    ]
    system_prompt = "\n\n".join(
        part
        for part in [
            base_system_prompt,
            _build_project_context_prompt(runtime_context),
            _build_job_workspace_prompt(workspace_dir),
            _build_runtime_context_prompt(),
            _build_skill_context_prompt() if used_skills else "",
            _frozen_skill_prompt(used_skills),
            _previous_result_prompt(job),
            memory_prompt,
            _build_final_answer_system_prompt(),
        ]
        if part
    )
    from src.scheduler.firing_toolkit import assemble_scheduled_firing_toolkit_spec

    spec = assemble_scheduled_firing_toolkit_spec(
        job, agent=agent_config, project_resources=project_resources,
    )
    runtime_tool_scope = deserialize_frozen_runtime_tool_scope(spec.get("tool_scope"))
    denylist = set(SCHEDULED_JOB_UNATTENDED_DENYLIST)
    claimed_mcp = set(spec.get("mcp_ids") or []) if spec.get("filter_claimed") else None
    claimed_http = None
    if spec.get("filter_claimed"):
        claimed_http = set(spec.get("http_ids") or []) | set(spec.get("knowledge_ids") or [])
    return ChatRunContext(
        session_id=session_id,
        runtime_context=runtime_context,
        project_resources=project_resources,
        agent_id=agent_id,
        agent_name=agent_name,
        agent_config=agent_config,
        persist_run=True,
        runtime_model_settings=runtime_model_settings,
        runtime_model_config=runtime_model_config,
        readiness=readiness,
        intent_route=intent_route,
        effective_user_message=frozen_prompt,
        used_skills=used_skills,
        skill_paths=skill_paths,
        used_skill_details=used_skill_details,
        skill_runs=[],
        active_skill_run=None,
        model_name=runtime_model_config.model_name,
        system_prompt=system_prompt,
        guardrails=guardrails,
        runtime_tool_scope=runtime_tool_scope,
        mcp_servers=_build_runtime_mcp_servers(
            agent_config,
            claimed_tool_ids=claimed_mcp,
        ),
        knowledge_tools=_build_runtime_knowledge_tools(
            agent_config,
            allowed_tool_ids=claimed_http,
        ),
        extra_filesystem_scopes=readable_scopes,
        session_workspace_dir=workspace_dir,
        session_artifact_paths_before=session_artifact_paths_before,
        trigger_source="scheduled_job",
        approval_mode=_require_job_approval_mode(job),
        tool_name_denylist=denylist,
        write_scope_dir=workspace_dir,
        scheduled_job_id=str(getattr(job, "id", "") or getattr(firing, "job_id", "") or ""),
        scheduled_firing_id=str(getattr(firing, "id", "") or ""),
        user_message_note=_build_file_attachments_user_note(run_data.files),
        memory_prompt=memory_prompt,
        memory_recall_text="",
    )


def _frozen_skill_prompt(skills: list[Any]) -> str:
    """把凍結 Skill 的 id 寫進本輪上下文，供 firing trace 與模型看到同一份列表。"""

    if not skills:
        return ""
    lines = ["## Frozen Skills"]
    for skill in skills:
        lines.append(f"- {getattr(skill, 'id', '')}: {getattr(skill, 'name', '')}")
    return "\n".join(lines)


def _previous_result_prompt(job: Any) -> str:
    """context_policy=fresh 不帶上一次結果；其他策略才讀 owner session 的最近回答。"""

    if str(getattr(job, "context_policy", "fresh") or "fresh") == "fresh":
        return ""
    session_id = str(getattr(job, "created_from_session_id", "") or "")
    if not session_id:
        return ""
    from src.storage.database import get_latest_assistant_chat_message

    record = get_latest_assistant_chat_message(session_id)
    text = str((record or {}).get("content") or "").strip()
    if not text:
        return ""
    return f"## Previous Result\n{text[:4000]}"


MAX_FORCED_SKILLS = 3


def resolve_forced_runtime_skills(
    skill_ids: list[str],
    *,
    project_resources: ProjectResourceAccess,
    external_skills: list[Skill] | None = None,
) -> list[Skill]:
    """解析发送框强制开启的 Skill：须已安装且启用。"""

    requested = list(dict.fromkeys(str(skill_id).strip() for skill_id in skill_ids if str(skill_id).strip()))
    if not requested:
        return []
    if len(requested) > MAX_FORCED_SKILLS:
        raise ValueError(f"本轮最多强制开启 {MAX_FORCED_SKILLS} 个 Skill。")

    by_id = {str(getattr(skill, "id", "") or ""): skill for skill in (external_skills or [])}
    resolved: list[Skill] = []
    missing: list[str] = []
    for skill_id in requested:
        skill = get_installed_skill(skill_id) or by_id.get(skill_id)
        if skill is None or not bool(getattr(skill, "enabled", True)):
            missing.append(skill_id)
            continue
        resolved.append(skill)
    if missing:
        raise ValueError(f"强制开启的 Skill 不存在或未启用：{', '.join(missing)}")
    del project_resources
    return resolved


def _merge_forced_skills(used_skills: list[Skill], forced_skills: list[Skill]) -> list[Skill]:
    """把本轮强制开启的 Skill 并入 catalog，已存在的保持原顺序。"""

    if not forced_skills:
        return used_skills
    merged = list(used_skills)
    seen = {str(getattr(skill, "id", "") or "") for skill in merged}
    for skill in forced_skills:
        skill_id = str(getattr(skill, "id", "") or "")
        if not skill_id or skill_id in seen:
            continue
        merged.append(skill)
        seen.add(skill_id)
    return merged


def _build_project_context_prompt(runtime_context: RuntimeContext) -> str:
    """构造 Project root 和项目说明提示。"""

    instructions = load_project_instructions(runtime_context)
    parts = [
        "# Project Context",
        f"Project slug: {runtime_context.project_slug}",
        f"Project name: {runtime_context.project_name}",
        f"Project root: {runtime_context.project_root}",
        "All file operations must remain within the effective project filesystem scope.",
    ]
    if instructions:
        parts.extend(["", "## Project Instructions", instructions])
    return "\n".join(parts)


def _enabled_scanned_skill_ids(skill_ids: list[str], external_skills: list[Skill]) -> list[str]:
    """保留已启用且扫描命中的 Skill，不查绑定表。"""

    scanned = {
        str(getattr(skill, "id", "") or ""): skill
        for skill in external_skills
    }
    kept: list[str] = []
    for skill_id in skill_ids:
        skill = scanned.get(skill_id) or get_installed_skill(skill_id)
        if skill is None or not bool(getattr(skill, "enabled", True)):
            continue
        kept.append(skill_id)
    return kept


def _skill_is_shared(skill) -> bool:
    """已启用包是否绕过 Agent 绑定进入合格 Chat 目录。"""

    from src.catalog.skills import ALWAYS_INJECTED_SKILL_IDS

    skill_id = str(getattr(skill, "id", "") or "")
    if skill_id in ALWAYS_INJECTED_SKILL_IDS:
        return True
    return bool(getattr(skill, "available_to_all_agents", True))


def _merge_file_catalog_skills(
    used_skills: list,
    *,
    external_skills: list,
    inject: bool,
    configured_skill_ids: list[str],
) -> list:
    """合格 Chat 合并项目 Skill、系统包与共享开关，不新增 CRUD 工具。

    - `yinli-catalog` 说明书无条件追加（file-catalog-v1 §9.2）。
    - 项目 `.agents/skills` 免 Project binding（§13.2）；未共享时仍须出现在 Agent 配置。
    - 其余已启用且 `availableToAllAgents` 的包并入单 Agent / Team Leader 目录。
    - `inject=False` 留给 Team Member 等不合格路径，不因开关追加。
    """

    if not inject:
        return used_skills
    from src.catalog.skill_seed import seed_system_skills
    from src.catalog.skills import SYSTEM_SKILL_IDS
    from src.server.routes.skills import get_installed_skill, list_installed_enabled_skills

    seed_system_skills()
    allowed = set(configured_skill_ids)
    merged = list(used_skills)
    seen = {getattr(skill, "id", "") for skill in merged}

    def _append(skill) -> None:
        skill_id = str(getattr(skill, "id", "") or "")
        if not skill_id or skill_id in seen:
            return
        merged.append(skill)
        seen.add(skill_id)

    for skill in external_skills:
        if getattr(skill, "source_scope", "") != "project" or not getattr(skill, "enabled", False):
            continue
        if skill.id not in allowed and not _skill_is_shared(skill):
            continue
        _append(skill)
    for skill_id in SYSTEM_SKILL_IDS:
        if skill_id in seen:
            continue
        skill = get_installed_skill(skill_id)
        if skill is None:
            continue
        if skill_id not in allowed and not _skill_is_shared(skill):
            continue
        _append(skill)
    for skill in list_installed_enabled_skills():
        if getattr(skill, "id", "") in SYSTEM_SKILL_IDS:
            continue
        if skill.id not in allowed and not _skill_is_shared(skill):
            continue
        _append(skill)
    return merged


def _build_session_workspace_prompt(session_workspace_dir: str) -> str:
    """声明本 Session 的默认产物目录。"""

    return "\n".join([
        "## Session Workspace",
        f"Default working directory for this session: {session_workspace_dir}",
        "Create every new deliverable with a relative path so it stays in this directory.",
        "Use an explicit Project path only when the user specifically asks to modify an existing Project file.",
    ])


def _build_job_workspace_prompt(workspace_dir: str) -> str:
    """声明本次 firing 的唯一默认产物目录，避免与 Session Workspace 双路径。"""

    return "\n".join([
        "## Scheduled Job Workspace",
        f"Default working directory for this scheduled run: {workspace_dir}",
        "Write final deliverables as relative paths under outputs/ (for example outputs/weekly-report.md).",
        "Do not use absolute paths.",
        "Create every new deliverable with a relative path so it stays in this directory.",
    ])
