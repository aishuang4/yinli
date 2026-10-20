"""旧 Skill 登记的一次性、可恢复迁移；正常目录读取绝不调用此模块。"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.skills.catalog import build_skill_catalog
from src.skills.models import SkillContext, SkillError
from src.skills.parser import parse_manual
from src.skills.policy import SkillOverride, policy_transaction, read_policy, update_policy


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".migration-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


def migration_preflight(contexts: list[SkillContext], records: list[dict], overrides: list[dict] = ()) -> dict:
    """只读报告：以真实路径和 owner 定位，不用同名猜测。"""

    snapshots = [build_skill_catalog(context, refresh=True) for context in contexts]
    rows = []
    for record in records:
        old_id = str(record.get("id") or "")
        owner = str(record.get("workspaceId") or record.get("workspace_id") or "")
        source = str(record.get("resourcePath") or record.get("resource_path") or "")
        matches = [candidate for snapshot in snapshots if snapshot.context.workspace_id == owner
                   for candidate in snapshot.candidates
                   if source and candidate.package_path == Path(source).expanduser().resolve()]
        identities = sorted({candidate.id for candidate in matches})
        reason = "mapped" if len(identities) == 1 else ("ambiguous" if identities else "missing")
        if not owner:
            reason = "unowned"
        rows.append({"legacyId": old_id, "workspaceId": owner, "sourcePath": source,
                     "targets": identities, "result": reason,
                     "bodyOnly": not matches and bool(record.get("content"))})
    return {"schemaVersion": 1, "records": rows, "overrideCount": len(overrides),
            "diagnostics": [item.model_dump() for snapshot in snapshots for item in snapshot.diagnostics]}


def migrate_skill_files(contexts: list[SkillContext], records: list[dict], overrides: list[dict] = (),
                        *, evidence: dict | None = None, checkpoint_hook=None) -> dict:
    """先备份、后原子落包/策略、round-trip 后提交 marker；可注入中断演练。"""

    if not contexts:
        return {"records": []}
    tenant = contexts[0].tenant_root.resolve()
    if any(context.tenant_root.resolve() != tenant for context in contexts):
        raise SkillError("skill_scope_denied", "一次迁移只能处理一个租户")
    state_root = tenant / "data/skill-file-migration-v1"
    state_root.mkdir(parents=True, exist_ok=True)
    with policy_transaction(state_root / "migration.json"):
        marker = state_root / "complete.json"
        if marker.is_file():
            return json.loads(marker.read_text(encoding="utf-8"))
        backup = state_root / "backup"
        backup.mkdir(exist_ok=True)
        if not (backup / "complete.json").is_file():
            _atomic_json(backup / "legacy.json", {"records": records, "overrides": list(overrides), "evidence": evidence or {}})
            # profiles、Job 定义、策略和统一受管包一起留存。
            for relative in ("skills", "workspaces"):
                source = tenant / relative
                target = backup / relative
                if source.is_dir() and not target.exists():
                    stage = backup / f".{relative}-staging"
                    if stage.exists():
                        shutil.rmtree(stage)
                    shutil.copytree(source, stage, symlinks=True)
                    stage.rename(target)
            _atomic_json(backup / "complete.json", {"createdAt": datetime.now(timezone.utc).isoformat()})
        if checkpoint_hook:
            checkpoint_hook("backed_up")
        by_workspace = {context.workspace_id: context for context in reversed(contexts)}
        policy_before = {key: read_policy(context.policy_path) for key, context in by_workspace.items()}
        # 归属先落策略。许可只指明旧包 owner，不因许可创造包。
        for workspace, context in by_workspace.items():
            ownership = {}
            for record in records:
                if str(record.get("workspaceId") or "") != workspace:
                    continue
                source = str(record.get("resourcePath") or "")
                if source:
                    path = Path(source).expanduser().resolve()
                    try:
                        relative = path.relative_to(tenant / "skills").as_posix()
                    except ValueError:
                        continue
                    ownership[relative] = workspace
            if ownership:
                update_policy(context.policy_path, lambda policy: policy.legacy_ownership.update(ownership))
        materialized = []
        failures = []
        for record in records:
            workspace = str(record.get("workspaceId") or "")
            context = by_workspace.get(workspace)
            source = str(record.get("resourcePath") or "")
            if context is None:
                failures.append({"legacyId": record.get("id"), "result": "unowned"})
                continue
            if source and (Path(source) / "SKILL.md").is_file():
                continue
            content = str(record.get("content") or "")
            if not content:
                continue
            # 仅迁移历史受管正文。未知外部路径的记录不能借迁移写入任意目录。
            if source and not Path(source).resolve().is_relative_to(tenant / "skills"):
                failures.append({"legacyId": record.get("id"), "result": "unproven_source"})
                continue
            try:
                name = str(record.get("name") or record.get("id") or "legacy")
                if not content.lstrip("\ufeff").startswith("---"):
                    import yaml
                    header = yaml.safe_dump({"name": name, "description": record.get("description") or name}, allow_unicode=True)
                    content = f"---\n{header}---\n{content}"
                parse_manual(content.encode(), name)
                relative = "legacy-" + hashlib.sha256(str(record.get("id")).encode()).hexdigest()[:24]
                target = tenant / "skills" / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                if not target.exists():
                    temp = Path(tempfile.mkdtemp(prefix=".migration-package-", dir=target.parent))
                    try:
                        (temp / "SKILL.md").write_text(content, encoding="utf-8")
                        temp.rename(target)
                    finally:
                        if temp.exists():
                            shutil.rmtree(temp)
                materialized.append({"legacyId": record.get("id"), "workspaceId": workspace, "target": str(target)})
            except (SkillError, OSError) as exc:
                failures.append({"legacyId": record.get("id"), "result": "invalid_body", "reason": str(exc)})
        if checkpoint_hook:
            checkpoint_hook("packages_written")
        report = migration_preflight(contexts, records, overrides)
        snapshots = [build_skill_catalog(context, refresh=True) for context in contexts]
        for row in report["records"]:
            if row["result"] == "missing":
                target = next((item["target"] for item in materialized if item["legacyId"] == row["legacyId"]), None)
                if target:
                    row["targets"] = sorted({candidate.id for snapshot in snapshots
                                             if snapshot.context.workspace_id == row["workspaceId"]
                                             for candidate in snapshot.candidates if str(candidate.package_path) == target})
                    row["result"] = "mapped" if len(row["targets"]) == 1 else "missing"
        for workspace, context in by_workspace.items():
            relevant = [row for row in report["records"] if row["workspaceId"] == workspace and row["result"] == "mapped"]
            def configure(policy):
                for row in relevant:
                    target = row["targets"][0]
                    policy.legacy_aliases[row["legacyId"]] = sorted(set(policy.legacy_aliases.get(row["legacyId"], [])) | {target})
                    # 重跑不得覆盖迁移后用户已保存的新策略。
                    if target not in policy_before[workspace].overrides and target not in policy.overrides:
                        record = next(item for item in records if item.get("id") == row["legacyId"])
                        policy.overrides[target] = SkillOverride(enabled=bool(record.get("enabled", False)),
                            shared=bool(record.get("availableToAllAgents", True)), installed=record.get("status") == "installed")
                # 旧 external ID 由完整原始说明书的前 16 hex 派生；必须有唯一实际位置证据。
                candidates = {item.id: item for snapshot in snapshots if snapshot.context.workspace_id == workspace for item in snapshot.candidates}
                for override in overrides:
                    old = str(override.get("id") or "")
                    def old_ids(item):
                        if item.state == "invalid":
                            return set()
                        try:
                            normalized_text = item.source_path.read_text(encoding="utf-8")
                            normalized_hash = hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()
                        except (OSError, UnicodeError):
                            return set()
                        return {f"external-{item.content_hash[:16]}", f"external-{normalized_hash[:16]}"}
                    matches = [item for item in candidates.values() if old in old_ids(item)]
                    if len(matches) == 1:
                        target = matches[0].id
                        policy.legacy_aliases[old] = [target]
                        if target not in policy.overrides:
                            policy.overrides[target] = SkillOverride.model_validate({key: override[key] for key in ("enabled", "availableToAllAgents") if key in override})
                    elif matches:
                        policy.legacy_aliases[old] = sorted(item.id for item in matches)
                        failures.append({"legacyId": old, "result": "ambiguous", "targets": policy.legacy_aliases[old]})
                for job in (evidence or {}).get("jobs", []):
                    if str(job.get("workspace_id") or job.get("workspaceId") or "") != workspace:
                        continue
                    for frozen in job.get("required_skills", job.get("requiredSkills", [])) or []:
                        old = str(frozen.get("skillId") or frozen.get("skill_id") or "")
                        source = str(frozen.get("sourcePath") or frozen.get("source_path") or "")
                        if not old or not source:
                            continue
                        matching = [item for item in candidates.values() if item.source_path == Path(source).resolve()]
                        if len(matching) == 1:
                            target = matching[0].id
                            policy.legacy_aliases[old] = sorted(set(policy.legacy_aliases.get(old, [])) | {target})
                policy.migration = {"schemaVersion": 1, "state": "verified", "backup": str(backup),
                    "diagnostics": [{"code": "skill_" + row["result"], "message": "旧 Skill 迁移未能唯一定位，请检查文件和来源", "source": row["sourcePath"], "reference": row["legacyId"]}
                                    for row in report["records"] if row["workspaceId"] == workspace and row["result"] != "mapped"]}
            update_policy(context.policy_path, configure)
            # 验证已保存的配置确能从文件 round-trip，并且旧引用只有唯一映射。
            catalog = build_skill_catalog(context, refresh=True)
            for row in relevant:
                if not any(item.id == row["targets"][0] for item in catalog.candidates):
                    raise SkillError("skill_migration_failed", "迁移 round-trip 校验失败")
        report["failures"] = failures
        report["materialized"] = materialized
        _atomic_json(state_root / "report.json", report)
        if checkpoint_hook:
            checkpoint_hook("verified")
        _atomic_json(marker, report)
        return report


def migrate_tenant_skills() -> dict:
    """启动适配器；旧表仅在无完成 marker 时读取，保留历史数据不双写。"""

    from src.skills.catalog import current_skill_context
    from src.config.runtime_paths import tenant_root
    marker = tenant_root() / "data/skill-file-migration-v1/complete.json"
    if marker.is_file():
        from src.storage import database
        from src.skills.catalog import current_skill_context
        contexts = [current_skill_context() for project in database.list_projects(include_archived=True)]
        _pause_unresolved_jobs(contexts, database.list_scheduled_jobs())
        return json.loads(marker.read_text(encoding="utf-8"))
    from src.storage import database
    workspaces = database.list_workspaces(include_archived=True)
    projects = database.list_projects(include_archived=True)
    contexts = [current_skill_context() for workspace in workspaces]
    contexts += [current_skill_context() for project in projects]
    records = database.list_skills() or database.list_json_records("skills")
    if not records:
        legacy_file = tenant_root() / "data/skills.json"
        if legacy_file.is_file():
            try:
                legacy = json.loads(legacy_file.read_text(encoding="utf-8"))
                records = legacy if isinstance(legacy, list) else list(legacy.values())
            except (ValueError, AttributeError) as exc:
                raise SkillError("skill_migration_failed", "旧 skills.json 无法解析，未执行迁移") from exc
    overrides = database.list_json_records("external_skill_overrides")
    evidence = {"bindings": {str(project["id"]): database.list_project_resource_bindings(str(project["id"])) for project in projects},
                "jobs": database.list_scheduled_jobs(), "projects": projects}
    # SQLite 备份使用一致性 backup API，不能复制活跃 WAL 库文件。
    state_root = tenant_root() / "data/skill-file-migration-v1"
    backup = state_root / "backup"
    backup.mkdir(parents=True, exist_ok=True)
    sql_backup = backup / "database.sqlite"
    if not sql_backup.exists():
        import sqlite3
        temp = backup / ".database-staging.sqlite"
        with database._lock, database._connect() as source, sqlite3.connect(temp) as target:
            source.backup(target)
        temp.replace(sql_backup)
    report = migrate_skill_files(contexts, records, overrides, evidence=evidence)
    # marker 后再核对未确认的旧 Job；中断重试仍执行暂停，冻结 hash 永远不变。
    _pause_unresolved_jobs(contexts, evidence["jobs"])
    return report


def _pause_unresolved_jobs(contexts, jobs):
    """迁移不能猜旧引用，也不能给缺失 manifest 的任务补写当前 hash。"""
    from src.skills import resolve_skill
    from src.scheduler.service import ScheduledJobService
    service = ScheduledJobService()
    for row in jobs:
        if row.get("status") not in {"active", "pending_confirmation"}:
            continue
        workspace = str(row.get("workspace_id") or row.get("workspaceId") or "default")
        project = str(row.get("project_id") or row.get("projectId") or "default")
        context = next((item for item in contexts if item.workspace_id == workspace and item.project_id == project), None)
        refs = row.get("skill_ids", row.get("skillIds", [])) or []
        contract = row.get("capability_contract", row.get("capabilityContract", {})) or {}
        refs = list(dict.fromkeys([*refs, *(contract.get("skill_ids", contract.get("skillIds", [])) or [])]))
        if not refs:
            continue
        reason = ""
        if context is None:
            reason = "skill_metadata_missing"
        else:
            catalog = build_skill_catalog(context)
            for ref in refs:
                try:
                    resolve_skill(ref, catalog)
                except SkillError as exc:
                    reason = exc.code
                    break
        if not reason and not row.get("required_skills", row.get("requiredSkills", [])):
            reason = "skill_metadata_missing"
        if reason:
            service.pause(str(row["id"]), reason=reason)


def restore_skill_backup(tenant: Path, *, database_path: Path | None = None) -> Path:
    """停止服务后的显式回滚；先完整保留新模式文件，再恢复同一批备份。"""
    import sqlite3
    tenant = tenant.resolve()
    state = tenant / "data/skill-file-migration-v1"
    backup = state / "backup"
    if not (backup / "complete.json").is_file():
        raise SkillError("skill_migration_failed", "没有完整的 Skill 迁移备份")
    exported = state / ("before-rollback-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f"))
    exported.mkdir()
    with policy_transaction(state / "migration.json"):
        for relative in ("skills", "workspaces"):
            current, original = tenant / relative, backup / relative
            if current.exists():
                shutil.copytree(current, exported / relative, symlinks=True)
            stage = tenant / f".rollback-{relative}"
            if stage.exists():
                shutil.rmtree(stage)
            if original.exists():
                shutil.copytree(original, stage, symlinks=True)
            if current.exists():
                shutil.rmtree(current)
            if stage.exists():
                stage.rename(current)
        if database_path is not None and (backup / "database.sqlite").exists():
            with sqlite3.connect(database_path) as source, sqlite3.connect(exported / "database.sqlite") as dest:
                source.backup(dest)
            with sqlite3.connect(backup / "database.sqlite") as source, sqlite3.connect(database_path) as dest:
                source.backup(dest)
        _atomic_json(exported / "rollback.json", {"backup": str(backup), "state": "restored"})
    return exported
