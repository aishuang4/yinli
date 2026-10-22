"""构造群聊 Worker。跑一轮在 `worker_runtime.py`。"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from agentscope.permission import PermissionMode

from src.agents.runtime_types import MAX_REACT_ITERS, AgentGuardrails, AgentModelOptions, RuntimeToolScope
from src.config.runtime_settings import get_effective_model_settings
from src.group_studio.files import ensure_studio_dirs
from src.group_studio.tools_worker import acquire_worker_tool_context, worker_tools
from src.runtime_context import resolve_runtime_context
from src.security.permissions import build_permission_profile
from src.runtime.agent_resources import build_runtime_knowledge_tools, build_runtime_mcp_servers
from src.runtime.agent_spec import RuntimeModelConfig, build_main_agent_runtime_spec
from src.runtime.governance import build_actor_memory_prompt, build_context_governance_middlewares

WORKER_MAX_REACT_ITERS = MAX_REACT_ITERS
WORKER_DENYLIST = {"AskUser"}
WORKER_BASE_TOOLS = {
    "Read",
    "Write",
    "Edit",
    "Grep",
    "Glob",
    "Bash",
    "HttpRequest",
    "WebFetch",
    "timeline_retrieve",
    "timeline_ingest",
}


def build_worker_runtime_spec(snapshot: dict[str, Any], member: dict[str, Any]):
    """构造 Worker spec：复制 Agent 能力，群聊内工具权限全部放开。"""

    agent_row = _agent_row(str(member.get("agentId") or "default"))
    session_id = str(snapshot.get("chatSessionId") or "")
    resolve_runtime_context(
        project_slug=str(snapshot.get("projectSlug") or "") or None,
        session_id=session_id,
        run_id="",
        agent_id=str(agent_row.get("id") or member.get("agentId") or "default"),
    )
    studio_root = ensure_studio_dirs(session_id, str(snapshot["id"]))
    workspace = str((studio_root / "workspace").resolve())
    raw_guardrails = dict(agent_row.get("guardrails") or {})
    guardrails = AgentGuardrails(
        filesystem_scope=workspace,
        shell_blacklist=list(raw_guardrails.get("shell_blacklist") or ["rm -rf /", "sudo"]),
        network_enabled=True,
        execution_backend=str(raw_guardrails.get("execution_backend") or "local"),
        docker_image=str(raw_guardrails.get("docker_image") or "python:3.11-slim"),
        persistent_sandbox=bool(raw_guardrails.get("persistent_sandbox", False)),
        timeout_seconds=int(raw_guardrails.get("timeout_seconds") or 300),
    )
    guardrails = AgentGuardrails(
        filesystem_scope=guardrails.filesystem_scope,
        # Worker 是 BYPASS，没有人工审批兜底：破坏性 shell 操作靠黑名单硬拦（bypass_immune）。
        shell_blacklist=_merge_shell_blacklist(list(guardrails.shell_blacklist)),
        network_enabled=True,
        execution_backend=guardrails.execution_backend,
        docker_image=guardrails.docker_image,
        persistent_sandbox=guardrails.persistent_sandbox,
        timeout_seconds=guardrails.timeout_seconds,
        permission_profile=build_permission_profile(guardrails),
    )
    # 能力不再按 Project binding 过滤：Agent 声明且已启用的 Skill、租户全局 MCP/HTTP 都可用。
    mcp_ids = [str(item) for item in (agent_row.get("mcpTools") or [])]
    knowledge_ids = [str(item) for item in (agent_row.get("knowledgeTools") or [])]
    cfg = SimpleNamespace(mcp_tools=mcp_ids, knowledge_tools=knowledge_ids)
    mcp_servers = build_runtime_mcp_servers(cfg)
    knowledge_tools = build_runtime_knowledge_tools(cfg)
    skill_ids = [str(item) for item in (agent_row.get("skills") or [])]
    skill_paths = _skill_paths(skill_ids)

    ctx = acquire_worker_tool_context(
        str(snapshot["id"]),
        str(member["id"]),
        str(agent_row.get("id") or member.get("agentId") or ""),
    )
    tools = worker_tools(ctx)
    allowlist = set(WORKER_BASE_TOOLS)
    allowlist.update(tool.name for tool in tools)
    allowlist.update(item.name for item in knowledge_tools)
    for server in mcp_servers:
        allowlist.update(server.enable_tools or [])

    network = bool(guardrails.network_enabled)
    scope = RuntimeToolScope(
        allow_mcp=bool(mcp_servers) and network,
        allow_knowledge=bool(knowledge_tools),
        allow_bash=True,
        allow_http=network,
        allow_web=network,
    )
    runtime = get_effective_model_settings()
    model_config = RuntimeModelConfig(
        model_name=runtime.default_model,
        fallback_names=list(runtime.fallback_models or []),
        model_options=AgentModelOptions(max_react_iters=WORKER_MAX_REACT_ITERS),
    )
    worker_agent_id = str(agent_row.get("id") or member.get("agentId") or "default")
    worker_agent_name = str(member.get("displayName") or agent_row.get("name") or "工人")
    worker_session_id = f"{snapshot['id']}:{member['id']}"
    # Worker 是 Chat catalog 里的 Agent：与单 Agent 一样带自己的 Markdown 记忆摘要（按工作室会话冻结）。
    memory_prompt = _worker_memory_prompt(
        snapshot,
        agent_id=worker_agent_id,
        agent_name=worker_agent_name,
        session_id=worker_session_id,
    )
    spec = build_main_agent_runtime_spec(
        agent_id=worker_agent_id,
        agent_name=worker_agent_name,
        session_id=worker_session_id,
        system_prompt=_with_memory_prompt(_worker_system_prompt(snapshot, member), memory_prompt),
        model_config=model_config,
        deep_thinking=True,
        used_skills=[],
        skill_paths=skill_paths,
        guardrails=guardrails,
        mcp_servers=mcp_servers,
        knowledge_tools=knowledge_tools,
        runtime_tool_scope=scope,
        guardrail_cache_key=f"group-studio-worker-bypass:{guardrails.permission_profile.cache_key()}",
        runtime_features_cache_key="group-studio-worker-v2",
        extra_tools=tools,
        tool_name_allowlist=allowlist,
        tool_name_denylist=WORKER_DENYLIST,
        # 已读 Skill 正文保留 + 一级无模型整理 + 压缩可观测，与单 Agent 同一套。
        middlewares=build_context_governance_middlewares(skill_context=True, hard_stop=True),
        include_default_tools=True,
        workspace_dir=workspace,
        write_scope_dir=workspace,
        extra_filesystem_scopes=[],
    )
    return spec, ctx, tools


# 群聊 Worker 额外禁止的破坏性 shell 片段（子串匹配）。写文件仍限制在工作室 workspace 目录。
WORKER_SHELL_BLACKLIST: tuple[str, ...] = (
    "rm -rf",
    "rm -fr",
    "rm -r ",
    "rmdir ",
    "git reset --hard",
    "git clean -f",
    "git push --force",
    "git push -f",
    "mkfs",
    "dd if=",
    "> /dev/",
    "chmod -R 777",
    "chown -R",
    ":(){",
    "shutdown",
    "reboot",
)


def _merge_shell_blacklist(base: list[str]) -> list[str]:
    merged = [item for item in base if str(item).strip()]
    for item in WORKER_SHELL_BLACKLIST:
        if item not in merged:
            merged.append(item)
    return merged


def _agent_row(agent_id: str) -> dict[str, Any]:
    if agent_id == "default":
        return {
            "id": "default",
            "name": "默认助手",
            "guardrails": {},
            "mcpTools": [],
            "knowledgeTools": [],
            "skills": [],
        }
    from src.server.routes.agents import get_registered_agent

    agent = get_registered_agent(agent_id)
    if agent is not None:
        return agent.model_dump(by_alias=True)
    return {
        "id": agent_id,
        "name": agent_id,
        "guardrails": {},
        "mcpTools": [],
        "knowledgeTools": [],
        "skills": [],
    }


def _skill_paths(skill_ids: list[str]) -> list[str]:
    if not skill_ids:
        return []
    try:
        from src.server.routes.skills import get_installed_skill, resolve_runtime_skill_paths

        skills = []
        for skill_id in skill_ids:
            item = get_installed_skill(skill_id)
            if item is not None and bool(getattr(item, "enabled", True)):
                skills.append(item)
        from src.runtime.capabilities import resolve_capabilities

        resolved = resolve_capabilities(
            mode="interactive",
            requested_skill_ids=skill_ids,
            candidates=skills,
        )
        return list(resolve_runtime_skill_paths(list(resolved.skills)) or [])
    except Exception:
        return []


def _with_memory_prompt(system_prompt: str, memory_prompt: str) -> str:
    return f"{system_prompt}\n\n{memory_prompt}" if memory_prompt else system_prompt


def _worker_memory_prompt(snapshot: dict[str, Any], *, agent_id: str, agent_name: str, session_id: str) -> str:
    """Worker 注入这个人的习惯和自己的专业全文。"""

    del agent_name
    try:
        return build_actor_memory_prompt(user_id="default", agent_id=agent_id, session_id=session_id)
    except Exception:
        return ""


def _worker_system_prompt(snapshot: dict[str, Any], member: dict[str, Any]) -> str:
    pin = snapshot.get("pin") or snapshot.get("taskCard") or {}
    names = [
        str(item.get("displayName") or item.get("agentId") or "")
        for item in (snapshot.get("members") or [])
        if item.get("status") != "removed"
    ]
    return (
        f"你是群聊成员「{member.get('displayName') or member.get('agentId')}」，不是主持人。\n"
        "说完必须调用 group_post_message。需要同伴接着说时，可带对方 membershipId 作为 nextMemberId；"
        "不要指挥全场，也不要等主持人下令。\n"
        f"置顶：{pin.get('goal') or pin.get('title') or '（无）'}\n"
        f"备注：{pin.get('notes') or pin.get('background') or ''}\n"
        f"当前成员：{names}\n"
        "对群可见的发言只写结论，不要把内心推理写进正文。\n"
        "不要调用 AskUser。"
    )


def worker_permission_mode(snapshot: dict[str, Any], member: dict[str, Any]) -> PermissionMode:
    """测试用：返回 Worker 生效的 AgentScope permission mode。"""

    spec, _ctx, _tools = build_worker_runtime_spec(snapshot, member)
    return spec.guardrails.permission_profile.to_agentscope_permission_mode()
