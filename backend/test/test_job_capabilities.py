"""階段 3：ConfirmJob 三源凍結與 firing 的檔案雜湊校驗。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.runtime.capabilities import SkillManifestError, freeze_job_skills, resolve_capabilities
from src.skills.registry import _upsert_external_override, reload_skill_cache
from src.storage.database import ensure_db, replace_skills


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr("src.config.runtime_paths.USER_DATA_ROOT", tmp_path / "user-data")
    ensure_db()
    yield
    from src.storage import database

    database.forget_db(database.db_path())


def _install(skill_id: str, *, body: str = "步驟") -> None:
    replace_skills({
        skill_id: {
            "id": skill_id,
            "name": skill_id,
            "description": skill_id,
            "content": body,
            "status": "installed",
            "enabled": True,
            "updatedAt": "2026-10-21T00:00:00+00:00",
        },
    })


def test_confirm_merges_explicit_required_and_claimed_without_shared_skills(isolated_db):
    _install("explicit-skill")
    _install("required-skill")
    _install("claimed-skill")
    _install("shared-skill")
    job = SimpleNamespace(
        id="job-merge",
        mode="agent",
        agent_id="agent-1",
        skill_ids=["explicit-skill"],
        capability_contract=SimpleNamespace(skill_ids=["claimed-skill"]),
    )
    agent = SimpleNamespace(
        skills=["explicit-skill"],
        job_toolkit={"required": [{"kind": "skill", "id": "required-skill"}], "optional": []},
    )
    frozen = freeze_job_skills(job, agent=agent)
    assert [item["id"] for item in frozen] == ["explicit-skill", "required-skill", "claimed-skill"]
    assert [item["origin"] for item in frozen] == ["explicit", "required", "claimed"]
    assert all(len(item["content_sha256"]) == 64 for item in frozen)
    assert "shared-skill" not in {item["id"] for item in frozen}


def test_empty_freeze_warns_and_returns_empty(isolated_db, caplog):
    job = SimpleNamespace(id="job-empty", mode="agent", agent_id="", skill_ids=[], capability_contract=None)
    with caplog.at_level("WARNING"):
        assert freeze_job_skills(job, agent=None) == []
    assert "凍結 Skill 為空" in caplog.text


def test_hash_change_missing_and_disabled_pause_codes(isolated_db):
    _install("frozen-skill", body="第一版")
    job = SimpleNamespace(
        id="job-hash",
        mode="agent",
        agent_id="agent-1",
        skill_ids=["frozen-skill"],
        capability_contract=None,
    )
    frozen = freeze_job_skills(job, agent=SimpleNamespace(skills=[], job_toolkit={}))
    resolved = resolve_capabilities(mode="frozen", agent=SimpleNamespace(skills=[]), frozen=frozen)
    assert [skill.id for skill in resolved.skills] == ["frozen-skill"]

    path = Path(frozen[0]["source_path"])
    path.write_text(path.read_text(encoding="utf-8").replace("第一版", "第二版"), encoding="utf-8")
    with pytest.raises(SkillManifestError) as changed:
        resolve_capabilities(mode="frozen", agent=SimpleNamespace(skills=[]), frozen=frozen)
    assert changed.value.code == "skill_hash_changed"

    path.write_text(path.read_text(encoding="utf-8").replace("第二版", "第一版"), encoding="utf-8")
    _upsert_external_override("frozen-skill", enabled=False)
    reload_skill_cache()
    with pytest.raises(SkillManifestError) as disabled:
        resolve_capabilities(mode="frozen", agent=SimpleNamespace(skills=[]), frozen=frozen)
    assert disabled.value.code == "skill_disabled"

    _upsert_external_override("frozen-skill", enabled=True)
    reload_skill_cache()
    path.unlink()
    with pytest.raises(SkillManifestError) as missing:
        resolve_capabilities(mode="frozen", agent=SimpleNamespace(skills=[]), frozen=frozen)
    assert missing.value.code == "skill_missing"


def test_frozen_skill_outside_agent_ceiling_is_not_allowed(isolated_db):
    _install("frozen-skill")
    job = SimpleNamespace(
        id="job-ceiling",
        mode="agent",
        agent_id="agent-1",
        skill_ids=["frozen-skill"],
        capability_contract=None,
    )
    frozen = freeze_job_skills(job, agent=SimpleNamespace(skills=[], job_toolkit={}))
    with pytest.raises(SkillManifestError) as exc:
        resolve_capabilities(
            mode="frozen",
            agent=SimpleNamespace(skills=["other-skill"], job_toolkit={}),
            frozen=frozen,
        )
    assert exc.value.code == "skill_not_allowed"


def test_interactive_keeps_the_candidate_set(isolated_db):
    _install("kept-skill")
    from src.server.routes.skills import get_installed_skill

    skill = get_installed_skill("kept-skill")
    resolved = resolve_capabilities(
        mode="interactive",
        agent=SimpleNamespace(skills=[]),
        requested_skill_ids=["kept-skill"],
        candidates=[skill],
    )
    assert [item.id for item in resolved.skills] == ["kept-skill"]


def test_oversized_skill_cannot_be_frozen(isolated_db):
    _install("huge-skill", body="字" * 24_001)
    job = SimpleNamespace(
        id="job-huge",
        mode="agent",
        agent_id="default",
        skill_ids=["huge-skill"],
        capability_contract=None,
    )
    with pytest.raises(SkillManifestError) as exc:
        freeze_job_skills(job, agent=SimpleNamespace(skills=[], job_toolkit={}))
    assert exc.value.code == "skill_context_budget_exceeded"


def test_confirm_firing_trace_then_hash_change_pauses(isolated_db, monkeypatch):
    """創建 → 確認能看到凍結列表 → firing 上下文與 trace 帶上 Skill → 雜湊漂移暫停並通知。"""

    from src.project_files import project_job_run_directory
    from src.runtime_context import resolve_runtime_context
    from src.scheduler.chat_bridge import ScheduledChatLaunchError
    from src.scheduler.firing import run_firing
    from src.scheduler.service import ScheduledJobService
    from src.server.routes.chat.initial_events import emit_start_events
    from src.server.routes.chat.models import ChatMessage
    from src.server.routes.chat.run_context import build_run_context
    from src.storage.database import get_project, list_run_events, list_user_notifications

    _install("report-skill", body="寫周報\n")
    monkeypatch.setattr(
        "src.server.routes.chat.run_context.get_effective_model_settings",
        lambda: SimpleNamespace(default_model="model", fallback_models=[], provider="deepseek", api_key="x"),
    )
    monkeypatch.setattr("src.runtime.governance.build_habit_prompt", lambda *args, **kwargs: "")
    monkeypatch.setattr("src.runtime.governance.build_professional_prompt", lambda *args, **kwargs: "")
    project = get_project("default")
    assert project is not None
    monkeypatch.setattr(
        "src.server.routes.chat.run_context.get_registered_agent",
        lambda agent_id: SimpleNamespace(
            name="Assistant",
            system_prompt="prompt",
            skills=[],
            model_config_data=None,
            mcp_tools=[],
            knowledge_tools=[],
            guardrails=SimpleNamespace(filesystem_scope=project["primaryPath"]),
        ),
    )
    service = ScheduledJobService()
    job = service.create_job(
        project_slug="default",
        name="周报",
        agent_id="default",
        schedule_kind="once",
        schedule_expr={"at": "2026-10-22T09:00:00"},
        timezone="Asia/Shanghai",
        frozen_prompt="使用技能写周报",
        skill_ids=["report-skill"],
        persist_file=False,
    )
    confirmed = service.confirm(job.id, persist_file=False)
    assert [item["id"] for item in confirmed.required_skills] == ["report-skill"]
    assert len(confirmed.required_skills[0]["content_sha256"]) == 64
    assert confirmed.context_policy == "fresh"

    runtime_context = resolve_runtime_context(
        project_slug="default",
        session_id="owner-session",
        run_id="run-report",
        agent_id="default",
    )
    workspace = project_job_run_directory(Path(project["primaryPath"]), confirmed.id, "firing-report")
    run_data = ChatMessage(
        session_id="owner-session",
        project_slug="default",
        message=confirmed.frozen_prompt,
        agent_id="default",
    )

    async def load_and_trace():
        ctx = await build_run_context(
            run_data,
            runtime_context,
            mode="frozen",
            job=confirmed,
            firing=SimpleNamespace(id="firing-report", job_id=confirmed.id),
            session_workspace_dir=str(workspace),
        )
        async for _event in emit_start_events(ctx, run_data):
            pass
        return ctx

    ctx = asyncio.run(load_and_trace())
    assert [skill.id for skill in ctx.used_skills] == ["report-skill"]
    events = list_run_events("owner-session")
    route = next(item for item in events if item.get("eventType") == "INTENT_ROUTE")
    assert "runtimeSkills=1" in str(route.get("content") or "")
    assert route["payload"]["runtimeSkillIds"] == ["report-skill"]

    firing = service.create_firing(
        job_id=confirmed.id,
        scheduled_for=confirmed.next_run_at or "",
        trigger="schedule",
    )

    async def boom(_req):
        raise ScheduledChatLaunchError("skill_hash_changed: report-skill")

    monkeypatch.setattr("src.scheduler.firing.start_scheduled_chat_run", boom)
    asyncio.run(run_firing(
        confirmed.id,
        firing.id,
        service=service,
        semaphore=asyncio.Semaphore(1),
    ))
    paused = service.get_job(confirmed.id)
    assert paused.status == "paused"
    assert paused.pause_reason == "skill_hash_changed"
    notes = list_user_notifications(project_slug="default")
    assert any(item.get("kind") == "job_paused" for item in notes)
