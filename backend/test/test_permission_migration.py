"""隔离租户上的权限迁移与回滚，不接触真实用户配置。"""

from src.storage.records.chat_run_records import upsert_chat_run
from src.storage.records.project_records import create_project

import json
from pathlib import Path

import pytest
import yaml

from src.catalog.agent_models import Guardrails
from src.config.runtime_paths import agents_root, tenant_root
from src.scheduler.service import ScheduledJobService
from src.security.authorization.migration import (
    legacy_task_mode, migrate_permission_configuration, rollback_permission_configuration,
)
from src.storage.engine import database


@pytest.mark.parametrize("source,expected", [
    ({"tool_permission_profile": "trusted_tools"}, "full_access"),
    ({"toolPermissionMode": "bypass"}, "full_access"),
    ({"tool_permission_profile": "trusted_tools", "humanApproval": True}, "manual"),
    ({"tool_permission_profile": "ask_every_time", "toolPermissionMode": "bypass"}, "manual"),
    ({"toolPermissionProfile": "read_only"}, "manual"),
    ({}, "manual"), ({"toolPermissionMode": "unknown"}, "manual"),
])
def test_legacy_task_mode_precedence(source, expected):
    assert legacy_task_mode(source) == expected


def _file(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    original = "---\n" + yaml.safe_dump(payload, allow_unicode=True) + "---\n\n保留正文\n"
    path.write_text(original)
    return original


def _payload(path):
    return yaml.safe_load(path.read_text().split("---", 2)[1])


def test_migration_backups_idempotence_and_configuration_rollback(tmp_path):
    database.ensure_db()
    project = tmp_path / "project"
    project.mkdir()
    created = create_project({"name": "迁移测试", "primaryPath": str(project)})
    profiles = agents_root() / "profiles"
    trusted = profiles / "trusted.md"
    original = _file(trusted, {"id": "trusted", "guardrails": {"tool_permission_mode": "bypass", "network_enabled": False}})
    readonly = profiles / "reader.md"
    _file(readonly, {"id": "reader", "guardrails": {"tool_permission_profile": "read_only"}})
    child = project / ".agents" / "subagents" / "child.md"
    _file(child, {"name": "child", "permissionMode": "plan", "disallowedTools": ["Bash"]})
    job_file = project / ".agents" / "jobs" / "old.md"
    _file(job_file, {"id": "old", "agent_id": "trusted"})
    explicit = project / ".agents" / "jobs" / "new.md"
    _file(explicit, {"id": "new", "agent_id": "trusted", "executionPolicy": {"approvalMode": "manual"}})
    service = ScheduledJobService()
    job = service.create_job(
        project_slug=created["slug"],
        cwd=str(project),
        name="任务",
        agent_id="system-websearch-job",
        schedule_kind="weekly",
        schedule_expr={"weekdays": ["mon"], "at": "10:00"},
        timezone="Asia/Shanghai",
        frozen_prompt="测试",
        persist_file=False,
    )
    with database._connect() as conn:
        conn.execute("UPDATE scheduled_jobs SET agent_id='trusted',execution_policy='{}' WHERE id=?", (job.id,))
        conn.commit()
    manifest = migrate_permission_configuration()
    assert manifest["status"] == "done"
    assert _payload(trusted)["guardrails"] == {"network_enabled": False}
    assert _payload(readonly)["guardrails"]["operation_scope"] == "read_only"
    assert _payload(child)["operationScope"] == "read_only"
    assert "permissionMode" not in _payload(child)
    assert _payload(job_file)["executionPolicy"]["approvalMode"] == "full_access"
    assert _payload(explicit)["executionPolicy"]["approvalMode"] == "manual"
    assert service.get_job(job.id).execution_policy.approval_mode == "full_access"
    assert migrate_permission_configuration() == manifest
    saved = tenant_root() / "backups" / "permission-v2" / "manifest.json"
    assert saved.stat().st_mode & 0o077 == 0
    assert json.loads(saved.read_text())["legacyAgentGuardrails"]["trusted"]["tool_permission_mode"] == "bypass"
    rollback_permission_configuration()
    assert trusted.read_text() == original
    with database._connect() as conn:
        assert conn.execute("SELECT execution_policy FROM scheduled_jobs WHERE id=?", (job.id,)).fetchone()[0] == "{}"
    with pytest.raises(ValueError, match="已回滚"):
        migrate_permission_configuration()


def test_migration_requires_no_active_run():
    database.ensure_db()
    upsert_chat_run({"id": "busy", "sessionId": "session"})
    with pytest.raises(ValueError, match="活跃 Run"):
        migrate_permission_configuration()


def test_legacy_guardrails_import_preserves_readonly_without_exporting_modes():
    guardrails = Guardrails.model_validate({"toolPermissionProfile": "read_only", "human_approval": True})
    exported = guardrails.model_dump(by_alias=True)
    assert exported["operationScope"] == "read_only"
    assert not {"human_approval", "toolPermissionMode", "toolPermissionProfile"} & exported.keys()
