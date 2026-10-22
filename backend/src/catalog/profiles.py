"""持久 Agent Markdown 编解码。身份禁令在 `role_invariants.py`。"""
from __future__ import annotations


import re
from pathlib import Path
from typing import Any

import yaml

from src.agents.runtime_types import MAX_REACT_ITERS
from src.config.runtime_paths import project_default_root
from src.catalog.agent_models import AgentResponse, Guardrails, PlanConfig
from src.catalog.models import ProfileParseResult
from src.catalog.paths import ID_PATTERN, MAX_PROFILE_BYTES, atomic_write_text, validate_agent_id
from src.catalog.role_invariants import apply_role_invariants
from src.catalog.system_defaults import utc_now
from src.scheduler.job_toolkit import (
    CHAT_ROLE,
    JOB_ROLE,
    parse_agent_role,
    parse_job_toolkit,
)

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(?P<yaml>.*?)\n---\s*\n?(?P<body>.*)\Z", re.DOTALL)
_IGNORED_KEYS = {
    "planConfig", "plan_config", "status",
    "systemManaged", "system_managed", "sourcePath", "sourceKind",
    "modelConfig", "model_config",
}


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in values:
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return _dedupe([str(item) for item in value])


def _guardrails_from_yaml(value: Any, warnings: list[str]) -> Guardrails:
    data = value if isinstance(value, dict) else {}
    filesystem_scope = str(data.get("filesystem_scope") or "").strip()
    if filesystem_scope in {"/", str(Path("/").resolve())}:
        warnings.append("filesystem_scope=/ 已按空字符串处理")
        filesystem_scope = ""
    payload = {
        "filesystem_scope": filesystem_scope or str(project_default_root()),
        "shell_blacklist": data.get("shell_blacklist"),
        "network_enabled": data.get("network_enabled"),
        "operation_scope": data.get("operation_scope") or (
            "read_only" if data.get("tool_permission_profile") == "read_only" else None
        ),
        "execution_backend": data.get("execution_backend"),
        "docker_image": data.get("docker_image"),
        "persistent_sandbox": data.get("persistent_sandbox"),
        "timeout_seconds": data.get("timeout_seconds"),
    }
    cleaned = {key: item for key, item in payload.items() if item is not None}
    if filesystem_scope == "":
        cleaned["filesystem_scope"] = ""
    parsed = Guardrails.model_validate(cleaned or {})
    from src.tenancy.capabilities import coerce_hosted_guardrails

    return coerce_hosted_guardrails(parsed)


def _dump_filesystem_scope(value: str) -> str:
    text = str(value or "").strip()
    if not text or text == str(project_default_root()) or text in {"/", str(Path("/").resolve())}:
        return ""
    return text


def parse_profile_markdown(
    text: str,
    *,
    path: Path,
    source_kind: str = "profile",
    **_extra: object,
) -> ProfileParseResult:
    """解析 profile Markdown。失败时 agent 为 None。未知键丢弃，不因此失败。"""

    del _extra
    warnings: list[str] = []
    errors: list[str] = []
    raw = text.encode("utf-8")
    if len(raw) > MAX_PROFILE_BYTES:
        return ProfileParseResult(None, [f"文件超过 {MAX_PROFILE_BYTES} 字节"], warnings)
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return ProfileParseResult(None, ["缺少 YAML frontmatter"], warnings)
    try:
        payload = yaml.safe_load(match.group("yaml")) or {}
    except yaml.YAMLError as exc:
        return ProfileParseResult(None, [f"YAML 解析失败: {exc}"], warnings)
    if not isinstance(payload, dict):
        return ProfileParseResult(None, ["YAML frontmatter 必须是对象"], warnings)

    unknown = [str(key) for key in payload if key not in {
        "id", "name", "role", "description", "avatar", "skills", "mcp",
        "knowledge_tools", "tags", "max_react_iters", "guardrails", "job_toolkit",
        "created_at", "updated_at",
        *_IGNORED_KEYS,
    }]
    if unknown:
        warnings.append("unknown_keys: " + ", ".join(unknown))

    agent_id = str(payload.get("id") or "").strip()
    name = str(payload.get("name") or "").strip()
    role = str(payload.get("role") or "").strip()
    if not agent_id:
        errors.append("缺少 id")
    elif not ID_PATTERN.match(agent_id):
        errors.append(f"非法 id: {agent_id}")
    stem = path.stem
    if agent_id and stem != agent_id:
        errors.append(f"文件名与 id 不一致: {stem} != {agent_id}")
    if not name or len(name) > 50:
        errors.append("name 必须为 1–50 字符")
    if role not in {CHAT_ROLE, JOB_ROLE}:
        errors.append("role 只允许 chat 或 job")
    description = str(payload.get("description") or "")
    if len(description) > 200:
        errors.append("description 最长 200")
    if errors:
        return ProfileParseResult(None, errors, warnings)

    try:
        max_iters = int(payload.get("max_react_iters") or 128)
    except (TypeError, ValueError):
        return ProfileParseResult(None, ["max_react_iters 必须是整数"], warnings)
    if max_iters < 1 or max_iters > MAX_REACT_ITERS:
        return ProfileParseResult(None, [f"max_react_iters 超出 1–{MAX_REACT_ITERS}"], warnings)

    created_at = str(payload.get("created_at") or "").strip() or utc_now()
    updated_at = str(payload.get("updated_at") or "").strip() or utc_now()
    body = match.group("body").rstrip() + "\n"
    agent = AgentResponse.model_validate({
        "id": agent_id,
        "name": name,
        "avatar": str(payload.get("avatar") or ""),
        "description": description,
        "system_prompt": body.rstrip("\n"),
        "modelConfig": {"maxReactIters": max_iters},
        "mcpTools": _string_list(payload.get("mcp")),
        "knowledgeTools": _string_list(payload.get("knowledge_tools")),
        "skills": _string_list(payload.get("skills")),
        "tags": _string_list(payload.get("tags")),
        "planConfig": PlanConfig(),
        "guardrails": _guardrails_from_yaml(payload.get("guardrails"), warnings),
        "status": "idle",
        "created_at": created_at,
        "updated_at": updated_at,
        "role": role,
        "jobToolkit": parse_job_toolkit(payload.get("job_toolkit")),
        "sourcePath": str(path.resolve()),
        "sourceKind": source_kind,
    })
    try:
        agent = apply_role_invariants(agent, warnings)
    except ValueError as exc:
        return ProfileParseResult(None, [str(exc)], warnings)
    return ProfileParseResult(agent, [], warnings)


def dump_profile_markdown(agent: AgentResponse) -> str:
    """序列化为 frontmatter + 提示词正文。"""

    validate_agent_id(agent.id)
    frontmatter = {
        "id": agent.id,
        "name": agent.name,
        "role": parse_agent_role(agent.role),
        "description": agent.description or "",
        "avatar": agent.avatar or "",
        "skills": list(agent.skills),
        "mcp": list(agent.mcp_tools),
        "knowledge_tools": list(agent.knowledge_tools),
        "tags": list(agent.tags),
        "max_react_iters": int(agent.model_config_data.max_react_iters),
        "guardrails": {
            "filesystem_scope": _dump_filesystem_scope(agent.guardrails.filesystem_scope),
            "shell_blacklist": list(agent.guardrails.shell_blacklist),
            "network_enabled": bool(agent.guardrails.network_enabled),
            "operation_scope": agent.guardrails.operation_scope,
            "execution_backend": agent.guardrails.execution_backend,
            "docker_image": agent.guardrails.docker_image,
            "persistent_sandbox": bool(agent.guardrails.persistent_sandbox),
            "timeout_seconds": int(agent.guardrails.timeout_seconds),
        },
        "job_toolkit": agent.job_toolkit.model_dump(),
        "created_at": agent.created_at,
        "updated_at": agent.updated_at,
    }
    dumped = yaml.safe_dump(frontmatter, allow_unicode=True, default_flow_style=False, sort_keys=False).strip()
    body = (agent.system_prompt or "").rstrip() + "\n"
    return f"---\n{dumped}\n---\n\n{body}"


def write_profile_file(path: Path, agent: AgentResponse) -> AgentResponse:
    """校验 round-trip 后原子写入。"""

    text = dump_profile_markdown(agent)
    parsed = parse_profile_markdown(
        text,
        path=path,
        source_kind=agent.source_kind or "profile",
    )
    if parsed.agent is None:
        raise ValueError("; ".join(parsed.errors) or "profile round-trip 失败")
    atomic_write_text(path, text)
    return parsed.agent.model_copy(update={"source_path": str(path.resolve())})
