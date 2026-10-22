"""两个托管租户的数据目录互不可见。"""
from __future__ import annotations

from src.storage.records.scheduled_job_records import get_user_notification
from src.storage.records.scheduled_job_records import insert_user_notification
from src.storage.records.group_studio_records import get_group_studio_snapshot
from src.storage.records.workflow_records import list_workflow_runs
from src.storage.records.workflow_records import create_workflow_run
from src.storage.records.chat_session_records import insert_chat_session_if_absent
from src.storage.records.chat_session_records import list_chat_sessions
from src.storage.records.chat_session_records import get_chat_session


from src.config.model_catalog import ModelCatalog, ModelEntry, ProviderConfig, _write, read_catalog
from src.config.runtime_paths import skill_packages_root
from src.memory.markdown_store import MarkdownMemoryStore
from src.scheduler.service import ScheduledJobService
from src.scheduler.settings import set_scheduled_jobs_settings
from src.storage.engine import database
from src.tenancy.context import TenantContext, use_tenant
from src.tenancy.lifecycle import ensure_tenant

pytest_plugins = ["test.hosted_support"]

USER_A = {"id": "usr_" + "1" * 32, "email": "iso-a@example.com", "role": "user", "status": "active"}
USER_B = {"id": "usr_" + "2" * 32, "email": "iso-b@example.com", "role": "user", "status": "active"}


def _session(session_id: str) -> dict:
    return {
        "id": session_id,
        "title": session_id,
        "agent_id": "default",
        "agent_name": "助手",
        "mode": "agent",
        
        "project_slug": "default",
        "kind": "chat",
        "created_at": "2026-08-21T00:00:00+00:00",
        "updated_at": "2026-08-21T00:00:00+00:00",
    }


def test_tenant_b_cannot_read_tenant_a_resources(hosted_env):
    ensure_tenant(USER_A)
    ensure_tenant(USER_B)
    with use_tenant(TenantContext.hosted(USER_A["id"])):
        insert_chat_session_if_absent(_session("sess-a"))
        insert_user_notification({
            "id": "ntf-a",
            "kind": "job_finished",
            "title": "A 的通知",
            
            "project_slug": "default",
        })
        create_workflow_run({"id": "wf-a", "title": "A 的任务流", "goal": "只有 A"})
        with database._lock, database._connect() as conn:
            conn.execute(
                """
                INSERT INTO group_studios (
                    id, project_slug, chat_session_id, title, status,
                    current_task_card_id, budget_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'open', 'task-a', '{}', ?, ?)
                """,
                (
                    "grp-a", "default", "sess-a", "A 的群",
                    "2026-08-21T00:00:00+00:00", "2026-08-21T00:00:00+00:00",
                ),
            )
        set_scheduled_jobs_settings(enabled=True, capability_gate=False)
        ScheduledJobService().create_job(
            
            project_slug="default",
            name="A 的任务",
            agent_id="system-websearch-job",
            schedule_kind="weekly",
            schedule_expr={"weekdays": ["fri"], "at": "18:00"},
            timezone="Asia/Shanghai",
            frozen_prompt="只有 A",
        )
        MarkdownMemoryStore().write_habits("default", "A 的习惯秘密")
        MarkdownMemoryStore().write_professional("default", "A 的专业区秘密")
        secret = skill_packages_root() / "only-a"
        secret.mkdir()
        (secret / "SKILL.md").write_text("---\nname: only-a\n---\n只有 A\n", encoding="utf-8")
        _write(ModelCatalog(
            providers=(ProviderConfig(
                id="openai",
                name="OpenAI",
                adapter="openai",
                baseUrl="https://api.openai.com/v1",
                apiKey="sk-tenant-a-secret",
            ),),
            models=(ModelEntry(
                id="gpt",
                providerId="openai",
                model="gpt",
                label="gpt",
            ),),
            defaultModelId="gpt",
        ))

    with use_tenant(TenantContext.hosted(USER_B["id"])):
        assert get_chat_session("sess-a") is None
        assert list_chat_sessions() == [] or all(
            item["id"] != "sess-a" for item in list_chat_sessions()
        )
        assert get_user_notification("ntf-a") is None
        assert all(item.get("id") != "wf-a" for item in list_workflow_runs())
        assert get_group_studio_snapshot("grp-a") is None
        names = {item.name for item in ScheduledJobService().list_jobs()}
        assert "A 的任务" not in names
        habits = MarkdownMemoryStore().read_habits("default").content
        professional = MarkdownMemoryStore().read_professional("default").content
        assert "A 的习惯秘密" not in habits
        assert "A 的专业区秘密" not in professional
        assert not (skill_packages_root() / "only-a").exists()
        catalog, _etag = read_catalog()
        rendered = catalog.model_dump_json()
        assert "sk-tenant-a-secret" not in rendered
        assert catalog.default_model_id != "gpt"
