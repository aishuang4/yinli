"""扫描 Agent profiles，维护进程内 catalog 缓存。"""
from __future__ import annotations


import hashlib
import logging
import unicodedata
from pathlib import Path
from threading import RLock

from src.agents.system_agents import is_system_agent_id
from src.catalog.agent_models import AgentResponse
from src.catalog.models import CatalogError, CatalogSnapshot, ProfileParseResult
from src.catalog.paths import (
    path_stays_inside,
    profile_path,
    profiles_dir,
    system_profile_path,
    system_profiles_dir,
)
from src.catalog.profiles import parse_profile_markdown, write_profile_file
from src.catalog.role_invariants import apply_role_invariants
from src.catalog.system_defaults import (
    DEFAULT_AGENT_PROMPT_VERSION_TAG,
    DEFAULT_WEBSEARCH_JOB_AGENT_VERSION_TAG,
    build_default_agent,
    build_default_websearch_job_agent,
    utc_now,
)

logger = logging.getLogger(__name__)
_lock = RLock()
_snapshot: CatalogSnapshot | None = None


def _file_fingerprint(path: Path) -> str:
    stat = path.stat()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    return f"{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}|{digest}"


def _accept_markdown(directory: Path, source_kind: str) -> tuple[list[tuple[Path, str]], list[CatalogError]]:
    """收集一个目录里的 profile，拒绝越界 symlink 与大小写冲突。"""

    if not directory.is_dir():
        return [], []
    found: list[tuple[Path, str]] = []
    errors: list[CatalogError] = []
    seen_case: dict[str, Path] = {}
    for path in sorted(directory.glob("*.md")):
        if path.name.startswith(".") or path.name.endswith(".tmp") or path.name.endswith(".bak"):
            continue
        if ".conflict-" in path.name:
            continue
        if not path_stays_inside(path, directory):
            errors.append(CatalogError(
                path=str(path),
                message="symlink 或解析路径指向 catalog 根外",
                agent_id=path.stem,
            ))
            continue
        key = unicodedata.normalize("NFC", path.name).casefold()
        if key in seen_case:
            errors.append(CatalogError(
                path=str(path),
                message=f"与 {seen_case[key].name} 大小写冲突，已忽略",
                agent_id=path.stem,
            ))
            continue
        seen_case[key] = path
        found.append((path, source_kind))
    return found, errors


def _collect_profile_files() -> tuple[list[tuple[Path, str]], list[CatalogError]]:
    """返回已接受的 profile 文件，以及 symlink / 大小写冲突。"""

    found: list[tuple[Path, str]] = []
    errors: list[CatalogError] = []
    for directory, source_kind in (
        (system_profiles_dir(), "system_overlay"),
        (profiles_dir(), "profile"),
    ):
        accepted, scan_errors = _accept_markdown(directory, source_kind)
        found.extend(accepted)
        errors.extend(scan_errors)
    return found, errors


def _compute_disk_fingerprint(files: list[tuple[Path, str]]) -> str:
    parts = [_file_fingerprint(path) for path, _kind in files]
    parts.append(DEFAULT_AGENT_PROMPT_VERSION_TAG)
    parts.append(DEFAULT_WEBSEARCH_JOB_AGENT_VERSION_TAG)
    parts.sort()
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:16]


def _merge_system_agent(base: AgentResponse, overlay: AgentResponse | None) -> AgentResponse:
    """代码模板为底，overlay 覆盖可配置字段，角色保持模板。"""

    if overlay is None:
        return base
    merged = base.model_copy(update={
        "name": overlay.name,
        "avatar": overlay.avatar,
        "description": overlay.description,
        "system_prompt": overlay.system_prompt,
        "model_config_data": overlay.model_config_data,
        "mcp_tools": overlay.mcp_tools,
        "knowledge_tools": overlay.knowledge_tools,
        "skills": overlay.skills,
        "tags": overlay.tags,
        "guardrails": overlay.guardrails,
        "job_toolkit": overlay.job_toolkit,
        "updated_at": overlay.updated_at,
        "created_at": overlay.created_at or base.created_at,
        "source_path": overlay.source_path,
        "source_kind": "system_overlay",
        "role": base.role,
    })
    warnings: list[str] = []
    return apply_role_invariants(merged, warnings)


def _scan() -> CatalogSnapshot:
    files, scan_errors = _collect_profile_files()
    fingerprint = _compute_disk_fingerprint(files)
    agents: dict[str, AgentResponse] = {}
    errors: list[CatalogError] = list(scan_errors)
    warnings: list[str] = []
    invalid_by_id: dict[str, str] = {}
    overlays: dict[str, AgentResponse] = {}

    for path, source_kind in files:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(CatalogError(path=str(path), message=f"读取失败: {exc}"))
            continue
        parsed: ProfileParseResult = parse_profile_markdown(
            text, path=path, source_kind=source_kind,
        )
        warnings.extend(f"{path}: {item}" for item in parsed.warnings)
        if parsed.agent is None:
            message = "; ".join(parsed.errors) or "解析失败"
            errors.append(CatalogError(path=str(path), message=message, agent_id=path.stem))
            invalid_by_id[path.stem] = message
            continue
        agent = parsed.agent
        if source_kind == "system_overlay":
            if not is_system_agent_id(agent.id):
                errors.append(CatalogError(
                    path=str(path),
                    message="非系统 Agent 不能放在 _system",
                    agent_id=agent.id,
                ))
                continue
            overlays[agent.id] = agent
            continue
        if is_system_agent_id(agent.id):
            errors.append(CatalogError(
                path=str(path),
                message="系统 Agent id 只能出现在 _system 目录",
                agent_id=agent.id,
            ))
            invalid_by_id[agent.id] = "系统 Agent id 只能出现在 _system 目录"
            continue
        existing = agents.get(agent.id)
        if existing is not None:
            errors.append(CatalogError(
                path=str(path),
                message=f"id 冲突，已被 {existing.source_path} 占用",
                agent_id=agent.id,
            ))
            continue
        agents[agent.id] = agent

    for factory in (build_default_agent, build_default_websearch_job_agent):
        built = factory()
        agents[built.id] = _merge_system_agent(built, overlays.get(built.id))

    return CatalogSnapshot(
        fingerprint=fingerprint,
        agents=agents,
        errors=errors,
        warnings=warnings,
        invalid_by_id=invalid_by_id,
    )


def ensure_loaded() -> CatalogSnapshot:
    """按磁盘 fingerprint 复用或重建缓存。"""

    global _snapshot
    with _lock:
        files, _scan_errors = _collect_profile_files()
        fingerprint = _compute_disk_fingerprint(files)
        if _snapshot is not None and _snapshot.fingerprint == fingerprint:
            return _snapshot
        _snapshot = _scan()
        return _snapshot


def invalidate_catalog() -> None:
    """丢掉进程内扫描缓存。"""

    global _snapshot
    with _lock:
        _snapshot = None


def catalog_fingerprint() -> str:
    """当前磁盘 catalog fingerprint。"""

    return ensure_loaded().fingerprint


def get_registered_agent(agent_id: str) -> AgentResponse | None:
    """按 id 取可用 Agent。"""

    return ensure_loaded().agents.get(str(agent_id or ""))


def get_profile_invalid_error(agent_id: str) -> str | None:
    """损坏 profile 的错误信息。"""

    return ensure_loaded().invalid_by_id.get(str(agent_id or ""))


def list_registered_agents() -> list[AgentResponse]:
    """全部可用 Agent。"""

    return list(ensure_loaded().agents.values())


def save_agent(agent: AgentResponse) -> AgentResponse:
    """写入 profile 或系统 overlay，并失效缓存。"""

    warnings: list[str] = []
    prepared = apply_role_invariants(agent.model_copy(update={"updated_at": utc_now()}), warnings)
    if is_system_agent_id(prepared.id):
        path = system_profile_path(prepared.id)
        prepared = prepared.model_copy(update={"source_kind": "system_overlay"})
    else:
        path = profile_path(prepared.id)
        prepared = prepared.model_copy(update={"source_kind": "profile"})
    if path.exists():
        existing = parse_profile_markdown(
            path.read_text(encoding="utf-8"),
            path=path,
            source_kind=prepared.source_kind,
        )
        if existing.agent is not None:
            prepared = prepared.model_copy(update={"created_at": existing.agent.created_at})
    written = write_profile_file(path, prepared)
    invalidate_catalog()
    from src.agents.factory import invalidate_agent

    invalidate_agent(written.id)
    return get_registered_agent(written.id) or written


def delete_agent(agent_id: str) -> None:
    """删除用户 profile。系统 Agent 抛 PermissionError。"""

    if is_system_agent_id(agent_id):
        raise PermissionError("系统 Agent 不能删除")
    existing = get_registered_agent(agent_id)
    if existing is None:
        raise FileNotFoundError(agent_id)
    path = Path(existing.source_path) if existing.source_path else profile_path(agent_id)
    if path.exists():
        path.unlink()
    invalidate_catalog()
    from src.agents.factory import invalidate_agent

    invalidate_agent(agent_id)


def import_sqlite_agent_record(data: dict) -> AgentResponse:
    """把一条 Agent 记录写成 profile。未知键由模型丢弃。"""

    payload = {
        "id": data["id"],
        "name": data.get("name") or data["id"],
        "avatar": data.get("avatar", ""),
        "description": data.get("description", ""),
        "system_prompt": data.get("system_prompt") or data.get("systemPrompt") or "You are a helpful assistant.",
        "modelConfig": data.get("modelConfig") or {},
        "mcpTools": data.get("mcpTools") or data.get("mcp_tools") or [],
        "knowledgeTools": data.get("knowledgeTools") or data.get("knowledge_tools") or [],
        "skills": data.get("skills") or [],
        "tags": data.get("tags") or [],
        "planConfig": data.get("planConfig") or {},
        "guardrails": data.get("guardrails") or {},
        "status": "idle",
        "created_at": data.get("created_at") or utc_now(),
        "updated_at": data.get("updated_at") or utc_now(),
        "role": data.get("role") or "chat",
        "jobToolkit": data.get("jobToolkit") or data.get("job_toolkit") or {},
    }
    return save_agent(AgentResponse.model_validate(payload))
