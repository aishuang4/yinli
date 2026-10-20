"""权限 V2 配置迁移：先备份再改写，按租户幂等，不转换历史审批许可。"""

from contextlib import closing
import hashlib
import json
from pathlib import Path
import re

import yaml

from src.catalog.paths import atomic_write_text
from src.config.runtime_paths import subagent_root, tenant_root, workspaces_catalog_root
from src.storage import database

_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)
_LEGACY = {"human_approval", "humanApproval", "tool_permission_mode", "toolPermissionMode",
           "tool_permission_profile", "toolPermissionProfile"}


def legacy_task_mode(guardrails: dict) -> str:
    """只用于旧任务迁移；humanApproval 优先，未知或缺失保守人工。"""
    if guardrails.get("human_approval") or guardrails.get("humanApproval"):
        return "manual"
    profile = guardrails.get("tool_permission_profile") or guardrails.get("toolPermissionProfile")
    mode = guardrails.get("tool_permission_mode") or guardrails.get("toolPermissionMode")
    if profile:
        return "full_access" if profile == "trusted_tools" else "manual"
    return "full_access" if mode == "bypass" else "manual"


def _document(path: Path):
    text = path.read_text(encoding="utf-8")
    match = _FRONTMATTER.match(text)
    if path.suffix in {".yaml", ".yml"}:
        return yaml.safe_load(text), None
    if not match:
        return None, None
    return yaml.safe_load(match[1]), match[2]


def _dump(payload, body):
    data = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False).strip()
    return data + "\n" if body is None else f"---\n{data}\n---\n{body}"


def migrate_permission_configuration() -> dict:
    """仅停机切换调用；manifest 保存原文和计划，失败重试不丢旧策略证据。"""
    database.ensure_db()
    backup = tenant_root() / "backups" / "permission-v2"
    manifest_path = backup / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest["status"] == "rolled_back":
            raise ValueError("配置已回滚，请使用兼容旧版本启动，不自动重做权限迁移")
        if manifest["status"] == "done":
            return manifest
    else:
        with database._lock, closing(database._connect()) as conn:
            active = conn.execute("SELECT COUNT(*) FROM chat_runs WHERE status IN "
                                  "('running','waiting_user','waiting_permission','waiting_external')").fetchone()[0]
            if active:
                raise ValueError("权限迁移前必须完成或中止活跃 Run")
            jobs = [dict(row) for row in conn.execute("SELECT id,agent_id,execution_policy FROM scheduled_jobs")]
            sessions = [dict(row) for row in conn.execute("SELECT id,approval_mode FROM chat_sessions")]
            # 旧 SQLite Agent 正文不改写，只保留迁移依据；文件迁移后由原 catalog 处理正文。
            legacy_agents = {str(item["id"]): item.get("guardrails") or {} for item in database.list_agents()}
        roots = [workspaces_catalog_root()]
        for project in database.list_projects(include_archived=True):
            root = Path(str(project.get("rootPath") or "")) / ".agents"
            if root.is_dir():
                roots.append(root)
        roots.append(subagent_root())
        documents = {}
        agents = {"system-websearch-job": {"tool_permission_profile": "trusted_tools"}, **legacy_agents}
        for root in roots:
            if not root.is_dir():
                continue
            for path in root.rglob("*"):
                if path.suffix not in {".md", ".yaml", ".yml"} or path.is_symlink():
                    continue
                # 只接管权限定义，Skills、工具、用户正文和兼容用户目录不改写。
                if not any(part in {"profiles", "jobs", "subagents"} for part in path.parts):
                    continue
                payload, body = _document(path)
                if not isinstance(payload, dict):
                    continue
                documents[str(path.resolve())] = (payload, body)
                if "profiles" in path.parts:
                    agents[str(payload.get("id") or path.stem)] = payload.get("guardrails") or {}
        changes = []
        file_policies = {}
        for name, (payload, body) in documents.items():
            path = Path(name)
            revised = dict(payload)
            if "profiles" in path.parts:
                guardrails = dict(payload.get("guardrails") or {})
                if any(guardrails.get(key) == "read_only" for key in ("tool_permission_profile", "toolPermissionProfile")):
                    guardrails["operation_scope"] = "read_only"
                revised["guardrails"] = {key: value for key, value in guardrails.items() if key not in _LEGACY}
            elif "jobs" in path.parts and not (payload.get("executionPolicy") or payload.get("execution_policy")):
                actor = str(payload.get("agent_id") or payload.get("agentId") or "")
                revised["executionPolicy"] = {"approvalMode": legacy_task_mode(agents.get(actor, {}))}
            elif "subagents" in path.parts and "permissionMode" in payload:
                old = revised.pop("permissionMode")
                if old in {"plan", "explore", "read_only"}:
                    revised["operationScope"] = "read_only"
            if "jobs" in path.parts:
                policy = revised.get("executionPolicy") or revised.get("execution_policy")
                if policy:
                    file_policies[str(payload.get("id") or path.stem)] = policy
            if revised != payload:
                changes.append({"path": name, "content": _dump(revised, body)})
        for job in jobs:
            if not json.loads(job["execution_policy"] or "{}"):
                job["nextPolicy"] = file_policies.get(job["id"]) or {"approvalMode": legacy_task_mode(agents.get(job["agent_id"], {}))}
        backup.mkdir(parents=True, exist_ok=True, mode=0o700)
        files = []
        for name in [*documents, str(tenant_root() / "models.json")]:
            path = Path(name)
            if not path.is_file():
                continue
            copy = backup / (hashlib.sha256(name.encode()).hexdigest() + ".backup")
            copy.write_bytes(path.read_bytes())
            copy.chmod(0o600)
            files.append({"path": name, "backup": str(copy), "sha256": hashlib.sha256(copy.read_bytes()).hexdigest()})
        manifest = {"schemaVersion": 2, "status": "prepared", "files": files, "changes": changes,
                    "jobs": jobs, "sessions": sessions, "legacyAgentGuardrails": agents}
        atomic_write_text(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2))
        manifest_path.chmod(0o600)
    for change in manifest["changes"]:
        atomic_write_text(Path(change["path"]), change["content"])
    with database._lock, closing(database._connect()) as conn, conn:
        for job in manifest["jobs"]:
            if "nextPolicy" in job:
                conn.execute("UPDATE scheduled_jobs SET execution_policy=? WHERE id=? AND execution_policy IN ('','{}')",
                             (json.dumps(job["nextPolicy"]), job["id"]))
        conn.execute("UPDATE chat_sessions SET approval_mode='manual' WHERE approval_mode IS NULL OR approval_mode=''")
    manifest["status"] = "done"
    atomic_write_text(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2))
    manifest_path.chmod(0o600)
    from src.catalog.loader import invalidate_catalog

    invalidate_catalog()
    return manifest


def rollback_permission_configuration() -> None:
    """显式停机回滚配置；保留权限事实，不恢复 pending、不补执行 unknown。"""
    backup = tenant_root() / "backups" / "permission-v2" / "manifest.json"
    manifest = json.loads(backup.read_text(encoding="utf-8"))
    with database._lock, closing(database._connect()) as conn:
        active = conn.execute("SELECT COUNT(*) FROM chat_runs WHERE status IN "
                              "('running','waiting_user','waiting_permission','waiting_external')").fetchone()[0]
        if active:
            raise ValueError("回滚前必须停止活跃 Run")
    for item in manifest["files"]:
        content = Path(item["backup"]).read_bytes()
        if hashlib.sha256(content).hexdigest() != item["sha256"]:
            raise ValueError("配置备份校验失败")
    for item in manifest["files"]:
        atomic_write_text(Path(item["path"]), Path(item["backup"]).read_text(encoding="utf-8"))
    with database._lock, closing(database._connect()) as conn, conn:
        for job in manifest["jobs"]:
            conn.execute("UPDATE scheduled_jobs SET execution_policy=? WHERE id=?",
                         (job["execution_policy"], job["id"]))
    manifest["status"] = "rolled_back"
    atomic_write_text(backup, json.dumps(manifest, ensure_ascii=False, indent=2))
    backup.chmod(0o600)
