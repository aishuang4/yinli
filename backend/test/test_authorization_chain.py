"""父 Run、子執行者與 Job 重跑共用一條授權鏈。"""

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.agents.permission_adapter import PlatformAgent
from src.runtime.authorization import (
    RunAuthorization,
    inherit_child_authorization,
    open_run_binding,
)
from src.security.authorization.models import AuthorizationDecision
from src.security.authorization.risk import classify_risk
from src.storage import database
from src.storage.permission_records import PermissionRecordStore
from agentscope.message import ToolCallBlock

from src.tools.scoped_read import ScopedRead
from test.authorization_fakes import collect, make_agent, run_async
from test.test_permission_records import action_for


def test_child_inherits_mode_and_cannot_widen_parent_ceiling(tmp_path):
    parent_root = tmp_path / "project"
    member_dir = parent_root / "team" / "member"
    member_dir.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    parent = RunAuthorization(
        tenantId="local", workspaceId="w", projectId="p", sessionId="s",
        runId="run-parent", actorId="leader", approvalMode="model_review",
        capabilityVersion="cap-parent", surface="interactive",
    )
    with pytest.raises(ValueError, match="文件系统"):
        inherit_child_authorization(
            parent, actor_id="member",
            parent_filesystem_scope=str(parent_root),
            child_filesystem_scope=str(outside),
        )
    child, denied = inherit_child_authorization(
        parent, actor_id="member",
        parent_denied=frozenset({"Bash"}),
        child_denied=frozenset({"HttpRequest"}),
        parent_filesystem_scope=str(parent_root),
        child_filesystem_scope=str(member_dir),
    )
    assert child.approval_mode == "model_review"
    assert child.run_id == parent.run_id
    assert child.capability_version == "cap-parent"
    assert child.actor_id == "member"
    assert denied == frozenset({"Bash", "HttpRequest"})

    parent_binding = open_run_binding(parent, filesystem_scope=str(parent_root), denied_tool_names=frozenset({"Bash"}))
    child_binding = open_run_binding(
        child, filesystem_scope=str(parent_root), denied_tool_names=denied,
        parent_capabilities=parent_binding.service.boundary_check.capabilities,
        share_denials_from=parent_binding.service,
    )
    assert type(child_binding.service) is type(parent_binding.service)
    assert child_binding.service is not parent_binding.service
    assert child_binding.service._run_denials is parent_binding.service._run_denials
    assert child_binding.service._actor_denials is parent_binding.service._actor_denials


def test_delegate_is_platform_internal_and_child_tool_is_classified_alone():
    parent = action_for(approvalMode="manual")
    delegate = parent.model_copy(update={
        "tool_name": "DelegateTask", "tool_class": "DelegateTask", "effect": "orchestration",
    })
    child_read = parent.model_copy(update={
        "call_id": "child-read", "tool_name": "Read", "tool_class": "ScopedRead", "effect": "read_only",
    })
    assert classify_risk(delegate).risk_class == "platform_internal"
    assert classify_risk(child_read).risk_class == "trusted_readonly"


def test_finished_run_permission_cannot_authorize_the_next_job_run():
    store = PermissionRecordStore()
    first = action_for(approvalMode="full_access")
    second_owner = first.authorization.model_copy(update={"run_id": "run-next", "actor_id": "job"})
    database.upsert_chat_run({
        "id": second_owner.run_id, "workspaceId": "w", "projectId": "p", "sessionId": "s", "status": "running",
    })
    allowed = AuthorizationDecision(
        id=f"pd_{uuid4().hex}", actionFingerprint=first.fingerprint,
        verdict="allow", source="full_access", reasonCode="full_access", reason="开放",
    )
    saved = store.save(first, allowed)
    second = first.model_copy(update={"authorization": second_owner, "call_id": "next-call"})
    with pytest.raises(ValueError, match="归属"):
        store.reserve(second, saved.id)
    database.update_chat_run_status(first.authorization.run_id, "succeeded")
    with pytest.raises(ValueError, match="执行行动已变化"):
        store.reserve(first, saved.id)


def test_manual_rerun_creates_a_new_firing(isolated_db):
    from src.scheduler.service import ScheduledJobService
    from src.storage.database import DEFAULT_PROJECT_ID, DEFAULT_WORKSPACE_ID, ensure_db, upsert_agent

    ensure_db()
    upsert_agent({
        "id": "agent-1", "name": "agent-1", "system_prompt": "prompt",
        "workspaceId": DEFAULT_WORKSPACE_ID, "status": "idle",
    })
    service = ScheduledJobService()
    job = service.create_job(
        workspace_id=DEFAULT_WORKSPACE_ID,
        project_id=DEFAULT_PROJECT_ID,
        name="重新运行",
        agent_id="agent-1",
        schedule_kind="weekly",
        schedule_expr={"weekdays": ["fri"], "at": "18:00"},
        timezone="Asia/Shanghai",
        frozen_prompt="写周报",
    )
    confirmed = service.confirm(job.id, now=datetime(2026, 8, 20, tzinfo=timezone.utc))
    first = service.run_now(confirmed.id, now=datetime(2026, 8, 21, 1, 0, tzinfo=timezone.utc))
    service.complete_firing(first.id, "succeeded")
    second = service.run_now(confirmed.id, now=datetime(2026, 8, 21, 2, 0, tzinfo=timezone.utc))
    assert first.id != second.id
    assert first.trigger == "manual" and second.trigger == "manual"
    assert service.get_firing(first.id).status == "succeeded"


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr("src.config.runtime_paths.USER_DATA_ROOT", tmp_path / "user-data")
    yield
    database.forget_db(database.db_path())


@run_async
async def test_readonly_tool_uses_one_execution_function_in_every_mode(tmp_path, monkeypatch):
    target = tmp_path / "note.txt"
    target.write_text("same-bytes", encoding="utf-8")
    seen: list[tuple] = []
    original = ScopedRead.__call__

    async def wrapped(self, *args, **kwargs):
        seen.append((type(self).__module__, type(self).__name__, kwargs.get("file_path")))
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(ScopedRead, "__call__", wrapped)
    for mode in ("manual", "model_review", "full_access"):
        run_id = f"read-{mode}"
        database.upsert_chat_run({
            "id": run_id, "workspaceId": "w", "projectId": "p", "sessionId": "s", "status": "running",
            "authorization": {
                "schemaVersion": 1, "tenantId": "local", "approvalMode": mode, "reviewerConfigVersion": "",
            },
        })
        owner = RunAuthorization(
            tenantId="local", workspaceId="w", projectId="p", sessionId="s",
            runId=run_id, actorId="reader", approvalMode=mode, capabilityVersion="cap-read",
        )
        holder: dict = {}

        async def names(box=holder):
            agent = box.get("agent")
            if agent is None:
                return frozenset()
            return await agent.available_tool_names()

        binding = open_run_binding(owner, filesystem_scope=str(tmp_path), tool_names=names)
        tool = ScopedRead(base_dir=str(tmp_path), scope_dir=str(tmp_path))
        call = ToolCallBlock(id=f"read-{mode}", name="Read", input='{"file_path":"note.txt"}')
        agent = make_agent(
            tool, [call], PlatformAgent,
            authorization_service=binding.service, action_builder=binding.action_builder,
        )
        agent.bind_authorization(binding)
        holder["agent"] = agent
        await collect(agent._execute_tool_call(call))
        details = agent.authorization_details(call.id)
        assert details["executionStatus"] == "succeeded"
        assert details["approvalMode"] == mode
        assert details["riskClass"] == "trusted_readonly"
    assert [item[:2] for item in seen] == [("src.tools.scoped_read", "ScopedRead")] * 3
    assert all(str(item[2]).endswith("note.txt") for item in seen)
