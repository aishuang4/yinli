"""Workspace / Project Phase 0 至 Phase 2 回归测试。"""

from __future__ import annotations

import asyncio
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.datastructures import UploadFile

from src.agents.factory import AgentGuardrails
from src.memory.nodes import MemoryNodeService, MemoryNodeWrite
from src.project_resources import resolve_project_resource_access
from src.runtime_context import resolve_runtime_context
from src.project_files import (
    delete_project_session_files,
    list_project_session_artifacts,
    project_job_run_directory,
    project_session_directory,
    project_upload_directory,
    resolve_project_session_artifact,
    save_project_upload,
    validate_session_uploads,
)
from src.server.routes.chat.intent_router import IntentRoute
from src.server.routes.chat.models import ChatToolRun
from src.server.routes.chat.trace_recorder import build_tool_artifact_runtime_event
from src.server.routes.chat.handlers.runs import _get_project_run
from src.server.routes.chat.handlers.sessions import delete_session, list_session_catalog
from src.server.routes.chat.models import ChatMessage
from src.server.routes.chat.run_context import build_chat_run_context
from src.server.routes.chat.runtime import scope_guardrails_to_project
from src.storage.agent_defaults import is_stale_desktop_default_project_path
from src.server.routes.chat.store import _append_user_message
from src.server.routes.workspaces import (
    ProjectCreateRequest,
    ProjectResourceBindingRequest,
    WorkspaceBootstrapRequest,
    _validated_project_paths,
    bootstrap_workspace,
    create_project_record,
    put_project_resource,
)
from src.storage import database
from src.storage.database import (
    DEFAULT_PROJECT_ID,
    DEFAULT_WORKSPACE_ID,
    append_run_event,
    create_project,
    create_subagent_invocation,
    create_workspace,
    ensure_db,
    get_chat_session_ownership,
    get_project,
    get_workspace,
    list_chat_runs,
    list_memory_nodes,
    list_project_resource_bindings,
    list_subagent_invocations,
    list_workspaces,
    replace_mcp_servers,
    replace_skills,
    upsert_agent,
    upsert_chat_run,
    upsert_http_tool,
    upsert_project_resource_binding,
)


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    """为 Workspace / Project 测试使用独立 SQLite。"""

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "agent_workstation.db")
    monkeypatch.setattr(database, "_initialized", False)
    yield
    database._initialized = False


def _create_project(tmp_path, *, workspace_id: str, project_id: str, name: str = "项目") -> dict:
    root = tmp_path / project_id
    root.mkdir()
    return create_project({
        "id": project_id,
        "workspace_id": workspace_id,
        "name": name,
        "root_path": str(root),
    })


def test_default_workspace_and_project_are_created(isolated_db):
    ensure_db()

    workspace = get_workspace(DEFAULT_WORKSPACE_ID)
    project = get_project(DEFAULT_PROJECT_ID)

    assert workspace and workspace["status"] == "active"
    assert project and project["workspaceId"] == DEFAULT_WORKSPACE_ID
    assert project["rootPath"] == str(database.PROJECT_ROOT)


def test_startup_backfills_blank_ownership(isolated_db):
    ensure_db()
    _append_user_message(
        session_id="session-old",
        agent_id="agent-1",
        agent_name="Agent",
        mode="agent",
        model_name="model",
        content="历史消息",
        images=[],
    )
    upsert_chat_run({
        "id": "run-old",
        "sessionId": "session-old",
        "agentId": "agent-1",
        "status": "completed",
    })
    append_run_event(session_id="session-old", event_type="RUN_COMPLETED")
    MemoryNodeService(vector_backend=_NoopVectorBackend()).upsert_node(MemoryNodeWrite(
        id="memory-old",
        agent_id="agent-1",
        node_date="2026-07-15",
        title="历史记忆",
        summary="待回填归属",
    ))
    invocation = create_subagent_invocation({"id": "subagent-old", "session_id": "session-old"})

    with database._connect() as conn:
        for table in ("chat_sessions", "chat_runs", "run_events", "memory_nodes", "subagent_invocations"):
            conn.execute(f"UPDATE {table} SET workspace_id = '', project_id = ''")
        conn.commit()
    database._initialized = False
    ensure_db()

    assert get_chat_session_ownership("session-old") == {
        "workspaceId": DEFAULT_WORKSPACE_ID,
        "projectId": DEFAULT_PROJECT_ID,
    }
    assert list_chat_runs(project_id=DEFAULT_PROJECT_ID)[0]["projectId"] == DEFAULT_PROJECT_ID
    assert list_memory_nodes(project_id=DEFAULT_PROJECT_ID)[0]["project_id"] == DEFAULT_PROJECT_ID
    assert list_subagent_invocations(project_id=DEFAULT_PROJECT_ID)[0]["id"] == invocation["id"]


def test_project_api_validates_root_and_instructions(isolated_db, tmp_path):
    workspace = create_workspace({"id": "ws-docs", "name": "文档工作区"})
    root = tmp_path / "docs-project"
    root.mkdir()
    (root / "AGENTS.md").write_text("# Instructions", encoding="utf-8")

    project = asyncio.run(create_project_record(
        workspace["id"],
        ProjectCreateRequest(name="文档项目", rootPath=str(root), instructionsFile="AGENTS.md"),
    ))

    assert project.root_path == str(root.resolve())
    assert project.instructions_file == "AGENTS.md"
    with pytest.raises(HTTPException, match="instructionsFile"):
        _validated_project_paths(str(root), "../outside.md")
    with pytest.raises(HTTPException, match="Project root"):
        _validated_project_paths(str(root / "missing"))


def test_uploads_are_isolated_by_session(tmp_path):
    """附件路径使用 Session ID，且 Chat 不能引用其他 Session 的文件。"""

    uploaded = asyncio.run(save_project_upload(
        project_root=tmp_path,
        session_id="session-a",
        upload=UploadFile(filename="预算.xlsx", file=BytesIO(b"workbook")),
    ))

    assert str(uploaded["path"]).startswith(".agent-workstation/uploads/session-a/")
    output_dir = project_session_directory(tmp_path, "session-a")
    assert output_dir == tmp_path / ".agent-workstation" / "sessions" / "session-a"
    job_dir = project_job_run_directory(tmp_path, "job-a", "firing-a")
    assert job_dir == tmp_path / ".agent-workstation" / "jobs" / "job-a" / "runs" / "firing-a"
    validate_session_uploads(
        project_root=tmp_path,
        session_id="session-a",
        paths=[str(uploaded["path"])],
    )
    with pytest.raises(ValueError, match="不属于当前 Session"):
        validate_session_uploads(
            project_root=tmp_path,
            session_id="session-b",
            paths=[str(uploaded["path"])],
        )


def test_delete_project_session_files_only_removes_target_session(tmp_path):
    """清理会话文件时不能影响同一 Project 的其他 Session。"""

    target_upload = project_upload_directory(tmp_path, "session-target")
    target_output = project_session_directory(tmp_path, "session-target")
    target_upload.joinpath("source.docx").write_bytes(b"source")
    target_output.joinpath("result.docx").write_bytes(b"result")
    preserved_upload = project_upload_directory(tmp_path, "session-preserved")
    preserved_output = project_session_directory(tmp_path, "session-preserved")
    preserved_upload.joinpath("source.xlsx").write_bytes(b"source")
    preserved_output.joinpath("result.xlsx").write_bytes(b"result")

    assert delete_project_session_files(project_root=tmp_path, session_id="session-target") == 2
    assert not target_upload.exists()
    assert not target_output.exists()
    assert preserved_upload.is_dir()
    assert preserved_output.is_dir()


def test_session_artifacts_are_listed_and_resolved_within_session_directory(tmp_path):
    """脚本产物无需经过 Write/Edit，也应可被安全列出和下载。"""

    output_dir = project_session_directory(tmp_path, "session-artifacts")
    generated = output_dir / "reports" / "会议纪要.docx"
    generated.parent.mkdir()
    generated.write_bytes(b"word-document")
    outside = tmp_path / "outside.docx"
    outside.write_bytes(b"outside")

    artifacts = list_project_session_artifacts(
        project_root=tmp_path,
        session_id="session-artifacts",
    )

    assert artifacts == [{
        "path": ".agent-workstation/sessions/session-artifacts/reports/会议纪要.docx",
        "name": "会议纪要.docx",
        "extension": "DOCX",
        "size": len(b"word-document"),
    }]
    assert resolve_project_session_artifact(
        project_root=tmp_path,
        session_id="session-artifacts",
        relative_path=str(artifacts[0]["path"]),
    ) == generated
    with pytest.raises(HTTPException, match="不属于当前会话"):
        resolve_project_session_artifact(
            project_root=tmp_path,
            session_id="session-artifacts",
            relative_path="outside.docx",
        )


def test_session_artifacts_hide_internal_and_word_intermediates(tmp_path):
    """前台只列出用户可交付文件，不列出工具卸载、渲染校验和中间稿。"""

    output_dir = project_session_directory(tmp_path, "session-artifacts")
    (output_dir / "北京天气.docx").write_bytes(b"word")
    (output_dir / "北京天气.pdf").write_bytes(b"pdf")
    (output_dir / "北京天气.png").write_bytes(b"png")
    (output_dir / "source.md").write_text("# draft")
    (output_dir / "tool_result_call_00_abc.txt").write_text("offload")
    (output_dir / "tool_result-call_01.txt").write_text("offload")
    render = output_dir / "render"
    render.mkdir()
    (render / "page-1.png").write_bytes(b"page")
    (output_dir / "独立图表.png").write_bytes(b"chart")

    artifacts = list_project_session_artifacts(
        project_root=tmp_path,
        session_id="session-artifacts",
    )
    names = {item["name"] for item in artifacts}

    assert names == {"北京天气.docx", "独立图表.png"}


def test_write_artifact_event_skips_word_intermediates():
    """Write source.md / 工具卸载文件不进入前台产物 Block。"""

    hidden = build_tool_artifact_runtime_event(
        event_type="TOOL_RESULT_END",
        event_data={"tool_call_id": "c1"},
        tool_calls=[ChatToolRun(id="c1", name="Write", args='{"file_path":"source.md"}', status="completed")],
    )
    visible = build_tool_artifact_runtime_event(
        event_type="TOOL_RESULT_END",
        event_data={"tool_call_id": "c2"},
        tool_calls=[ChatToolRun(id="c2", name="Write", args='{"file_path":"北京天气.docx"}', status="completed")],
    )
    assert hidden is None
    assert visible is not None
    assert visible[0] == "ARTIFACT_CREATED"
    assert visible[1]["downloadPath"] == "北京天气.docx"


def test_delete_session_removes_its_uploaded_and_generated_files(isolated_db, tmp_path):
    """Chat 删除同时清理同一会话的附件和 Agent 产物目录。"""

    workspace = create_workspace({"id": "ws-delete", "name": "删除工作区"})
    project = _create_project(tmp_path, workspace_id=workspace["id"], project_id="project-delete")
    project_root = Path(project["rootPath"])
    _append_user_message(
        session_id="session-delete",
        agent_id="agent-1",
        agent_name="Agent",
        mode="agent",
        model_name="model",
        content="请处理文件",
        images=[],
        workspace_id=workspace["id"],
        project_id=project["id"],
    )
    uploaded = project_upload_directory(project_root, "session-delete")
    generated = project_session_directory(project_root, "session-delete")
    uploaded.joinpath("source.docx").write_bytes(b"source")
    generated.joinpath("result.docx").write_bytes(b"result")

    asyncio.run(delete_session("session-delete", project_id=project["id"]))

    assert not uploaded.exists()
    assert not generated.exists()
    assert all(item.id != "session-delete" for item in asyncio.run(list_session_catalog()))


def test_workspace_bootstrap_validates_project_before_creating_workspace(isolated_db, tmp_path):
    """组合创建必须先校验 root，避免留下没有 Project 的空 Workspace。"""

    before_ids = {item["id"] for item in list_workspaces()}
    with pytest.raises(HTTPException, match="Project root"):
        asyncio.run(bootstrap_workspace(WorkspaceBootstrapRequest(
            workspaceName="不会创建",
            name="无效项目",
            rootPath=str(tmp_path / "missing"),
        )))
    assert {item["id"] for item in list_workspaces()} == before_ids


def test_session_cannot_continue_in_another_project(isolated_db, tmp_path):
    create_workspace({"id": "ws-chat", "name": "Chat 工作区"})
    project_a = _create_project(tmp_path, workspace_id="ws-chat", project_id="project-a")
    project_b = _create_project(tmp_path, workspace_id="ws-chat", project_id="project-b")
    _append_user_message(
        session_id="session-a",
        agent_id="agent-1",
        agent_name="Agent",
        mode="agent",
        model_name="model",
        content="属于 A",
        images=[],
        workspace_id="ws-chat",
        project_id=project_a["id"],
    )

    context = resolve_runtime_context(
        project_id=project_a["id"],
        session_id="session-a",
        run_id="run-a",
        agent_id="agent-1",
    )
    assert context.project_root == (tmp_path / "project-a").resolve()
    with pytest.raises(ValueError, match="不能在 Project"):
        resolve_runtime_context(
            project_id=project_b["id"],
            session_id="session-a",
            run_id="run-b",
            agent_id="agent-1",
        )


def test_session_catalog_keeps_project_ownership(isolated_db, tmp_path):
    """历史目录可跨 Project 展示，但每条 Session 必须保留自己的归属。"""

    create_workspace({"id": "ws-catalog", "name": "历史目录"})
    for project_id in ("project-a", "project-b"):
        _create_project(tmp_path, workspace_id="ws-catalog", project_id=project_id)
        _append_user_message(
            session_id=f"session-{project_id}",
            agent_id="agent-1",
            agent_name="Agent",
            mode="agent",
            model_name="model",
            content=project_id,
            images=[],
            workspace_id="ws-catalog",
            project_id=project_id,
        )

    catalog = asyncio.run(list_session_catalog())
    ownership = {(item.id, item.workspace_id, item.project_id) for item in catalog}
    assert ownership == {
        ("session-project-a", "ws-catalog", "project-a"),
        ("session-project-b", "ws-catalog", "project-b"),
    }


def test_project_root_intersects_agent_filesystem_scope(tmp_path):
    project_root = tmp_path / "project"
    narrow_scope = project_root / "src"
    narrow_scope.mkdir(parents=True)

    project_scoped = scope_guardrails_to_project(
        AgentGuardrails(filesystem_scope=str(tmp_path)),
        project_root,
    )
    agent_scoped = scope_guardrails_to_project(
        AgentGuardrails(filesystem_scope=str(narrow_scope)),
        project_root,
    )

    assert project_scoped.filesystem_scope == str(project_root.resolve())
    assert agent_scoped.filesystem_scope == str(narrow_scope.resolve())
    with pytest.raises(ValueError, match="不相交"):
        scope_guardrails_to_project(
            AgentGuardrails(filesystem_scope=str(tmp_path / "other")),
            project_root,
        )


def test_hosted_shared_workspace_scope_narrows_to_user_project(tmp_path, monkeypatch):
    shared = tmp_path / "workspace"
    shared.mkdir()
    user_id = "usr_test"
    project_root = tmp_path / "users" / user_id / "projects" / "proj_test"
    project_root.mkdir(parents=True)
    monkeypatch.setattr("src.config.settings.settings.serve_mode", "hosted")
    monkeypatch.setattr("src.config.runtime_paths.DEFAULT_PROJECT_ROOT", shared)
    monkeypatch.setattr("src.server.auth.ownership.USER_DATA_ROOT", tmp_path)
    from src.server.auth.ownership import reset_owner, set_owner

    token = set_owner(user_id)
    try:
        scoped = scope_guardrails_to_project(
            AgentGuardrails(filesystem_scope=str(shared)),
            project_root,
        )
    finally:
        reset_owner(token)
    assert scoped.filesystem_scope == str(project_root.resolve())

    monkeypatch.setattr("src.config.settings.settings.serve_mode", "desktop")
    with pytest.raises(ValueError, match="不相交"):
        scope_guardrails_to_project(
            AgentGuardrails(filesystem_scope=str(shared)),
            project_root,
        )


def test_legacy_desktop_scope_migrates_only_when_missing(tmp_path):
    """历史桌面安装目录失效时迁移，用户仍存在的范围和未知错误不应被吞掉。"""

    project_root = tmp_path / "project"
    project_root.mkdir()
    legacy_scope = tmp_path / "agent-workstation-desktop" / "workspace"
    migrated = scope_guardrails_to_project(
        AgentGuardrails(filesystem_scope=str(legacy_scope)),
        project_root,
    )
    assert migrated.filesystem_scope == str(project_root.resolve())

    existing_scope = tmp_path / "agent-workstation-desktop-existing"
    existing_scope.mkdir()
    with pytest.raises(ValueError, match="不相交"):
        scope_guardrails_to_project(
            AgentGuardrails(filesystem_scope=str(existing_scope)),
            project_root,
        )


def test_stale_desktop_default_project_paths(tmp_path):
    """仅将空值、临时目录和失效的桌面默认路径视为待迁移。"""

    assert is_stale_desktop_default_project_path("")
    assert is_stale_desktop_default_project_path(str(tmp_path / "agent-workstation-desktop" / "workspace"))
    current = tmp_path / "project"
    current.mkdir()
    assert not is_stale_desktop_default_project_path(str(current))
    unrelated = tmp_path / "user-project"
    assert not is_stale_desktop_default_project_path(str(unrelated))


def test_runtime_records_are_project_scoped(isolated_db, tmp_path):
    create_workspace({"id": "ws-runtime", "name": "运行工作区"})
    _create_project(tmp_path, workspace_id="ws-runtime", project_id="project-a")
    _create_project(tmp_path, workspace_id="ws-runtime", project_id="project-b")

    for project_id in ("project-a", "project-b"):
        upsert_chat_run({
            "id": f"run-{project_id}",
            "sessionId": f"session-{project_id}",
            "workspaceId": "ws-runtime",
            "projectId": project_id,
            "status": "completed",
        })
        create_subagent_invocation({
            "id": f"subagent-{project_id}",
            "run_id": f"run-{project_id}",
            "session_id": f"session-{project_id}",
            "workspace_id": "ws-runtime",
            "project_id": project_id,
        })
        MemoryNodeService(vector_backend=_NoopVectorBackend()).upsert_node(MemoryNodeWrite(
            id=f"memory-{project_id}",
            workspace_id="ws-runtime",
            project_id=project_id,
            agent_id="agent-1",
            node_date="2026-07-15",
            title=project_id,
            summary="隔离测试",
        ))

    assert [item["id"] for item in list_chat_runs(project_id="project-a")] == ["run-project-a"]
    assert [item["id"] for item in list_subagent_invocations(project_id="project-a")] == ["subagent-project-a"]
    assert [item["id"] for item in list_memory_nodes(
        workspace_id="ws-runtime",
        project_id="project-a",
    )] == ["memory-project-a"]


def test_run_detail_actions_reject_another_project(monkeypatch):
    """Run 详情、订阅和动作必须校验当前 Project，而不只依赖 runId。"""

    async def fake_get_run(run_id: str):
        assert run_id == "run-a"
        return SimpleNamespace(project_id="project-a")

    monkeypatch.setattr(
        "src.server.routes.chat.handlers.runs.get_chat_background_run",
        fake_get_run,
    )

    assert asyncio.run(_get_project_run("run-a", "project-a")).project_id == "project-a"
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(_get_project_run("run-a", "project-b"))
    assert exc_info.value.status_code == 404


def test_new_project_snapshots_workspace_resources(isolated_db, tmp_path):
    """新 Project 只绑定创建时已经存在且属于同一 Workspace 的资源。"""

    create_workspace({"id": "ws-resources", "name": "资源工作区"})
    upsert_agent({
        "id": "agent-a",
        "name": "Agent A",
        "workspaceId": "ws-resources",
    })
    replace_skills({
        "skill-a": {
            "id": "skill-a",
            "name": "Skill A",
            "description": "",
            "category": "test",
            "status": "installed",
            "enabled": True,
            "updatedAt": "2026-07-16T00:00:00+00:00",
            "workspaceId": "ws-resources",
        },
    })
    replace_mcp_servers({
        "mcp-a": {
            "id": "mcp-a",
            "name": "MCP A",
            "description": "",
            "category": "test",
            "transport": "sse",
            "status": "installed",
            "enabled": True,
            "updatedAt": "2026-07-16T00:00:00+00:00",
            "workspaceId": "ws-resources",
        },
    })
    upsert_http_tool({
        "id": "http-a",
        "name": "HTTP A",
        "url": "https://example.com/api",
        "workspaceId": "ws-resources",
    })

    project = _create_project(
        tmp_path,
        workspace_id="ws-resources",
        project_id="project-resources",
    )
    bindings = {
        (item["resourceType"], item["resourceId"])
        for item in list_project_resource_bindings(project["id"])
        if item["enabled"]
    }
    assert bindings == {
        ("agent", "agent-a"),
        ("skill", "skill-a"),
        ("mcp", "mcp-a"),
        ("http_tool", "http-a"),
    }

    upsert_http_tool({
        "id": "http-later",
        "name": "稍后创建",
        "url": "https://example.com/later",
        "workspaceId": "ws-resources",
    })
    assert ("http_tool", "http-later") not in {
        (item["resourceType"], item["resourceId"])
        for item in list_project_resource_bindings(project["id"])
    }


def test_project_resource_access_enforces_enabled_bindings(isolated_db, tmp_path):
    """运行时白名单同时执行 enabled 和 Workspace 归属校验。"""

    create_workspace({"id": "ws-access", "name": "访问工作区"})
    upsert_agent({"id": "agent-access", "name": "Agent", "workspaceId": "ws-access"})
    project = _create_project(tmp_path, workspace_id="ws-access", project_id="project-access")
    upsert_project_resource_binding({
        "project_id": project["id"],
        "resource_type": "agent",
        "resource_id": "agent-access",
        "enabled": False,
    })
    context = resolve_runtime_context(
        project_id=project["id"],
        session_id="session-access",
        run_id="run-access",
        agent_id="agent-access",
    )
    access = resolve_project_resource_access(context)

    access.require_agent("agent-access")
    assert access.filter_ids("http_tool", ["unknown-http-tool"]) == []


def test_project_resource_api_rejects_cross_workspace_binding(isolated_db, tmp_path):
    """管理 API 不允许把其他 Workspace 的资源绑定进当前 Project。"""

    create_workspace({"id": "ws-a", "name": "A"})
    create_workspace({"id": "ws-b", "name": "B"})
    project = _create_project(tmp_path, workspace_id="ws-a", project_id="project-a")
    upsert_agent({"id": "agent-b", "name": "Agent B", "workspaceId": "ws-b"})

    with pytest.raises(HTTPException, match="不能绑定") as exc_info:
        asyncio.run(put_project_resource(
            project["id"],
            "agent",
            "agent-b",
            ProjectResourceBindingRequest(enabled=True),
        ))
    assert exc_info.value.status_code == 422

    with pytest.raises(HTTPException, match="不支持"):
        asyncio.run(put_project_resource(
            project["id"],
            "unknown",
            "agent-b",
            ProjectResourceBindingRequest(enabled=True),
        ))


def test_chat_run_context_applies_project_resource_allowlist(isolated_db, tmp_path, monkeypatch):
    """Chat 组装层只把 Project 白名单与 Agent 配置的交集交给 Runtime。"""

    create_workspace({"id": "ws-chat-resources", "name": "Chat 资源"})
    upsert_agent({
        "id": "agent-chat",
        "name": "Chat Agent",
        "workspaceId": "ws-chat-resources",
    })
    replace_skills({
        skill_id: {
            "id": skill_id,
            "name": skill_id,
            "description": "",
            "category": "test",
            "status": "installed",
            "enabled": True,
            "updatedAt": "2026-07-16T00:00:00+00:00",
            "workspaceId": "ws-chat-resources",
        }
        for skill_id in ("skill-allowed", "skill-blocked")
    })
    replace_mcp_servers({
        server_id: {
            "id": server_id,
            "name": server_id,
            "description": "",
            "category": "test",
            "transport": "sse",
            "status": "installed",
            "enabled": True,
            "updatedAt": "2026-07-16T00:00:00+00:00",
            "workspaceId": "ws-chat-resources",
        }
        for server_id in ("mcp-allowed", "mcp-blocked")
    })
    for tool_id in ("http-allowed", "http-blocked"):
        upsert_http_tool({
            "id": tool_id,
            "name": tool_id,
            "url": f"https://example.com/{tool_id}",
            "workspaceId": "ws-chat-resources",
        })
    project = _create_project(
        tmp_path,
        workspace_id="ws-chat-resources",
        project_id="project-chat-resources",
    )
    for resource_type, resource_id in (
        ("skill", "skill-blocked"),
        ("mcp", "mcp-blocked"),
        ("http_tool", "http-blocked"),
    ):
        upsert_project_resource_binding({
            "project_id": project["id"],
            "resource_type": resource_type,
            "resource_id": resource_id,
            "enabled": False,
        })

    captured: dict[str, object] = {}
    agent_config = SimpleNamespace(
        name="Chat Agent",
        system_prompt="system",
        skills=["skill-allowed", "skill-blocked"],
        mcp_tools=["mcp-tool-a"],
        knowledge_tools=["http-allowed", "http-blocked"],
        model_config_data=None,
        guardrails=SimpleNamespace(filesystem_scope=str(tmp_path)),
    )

    async def fake_readiness(**kwargs):
        captured["readiness_skill_ids"] = kwargs["skill_ids"]
        return SimpleNamespace(ready=True)

    async def fake_route(**kwargs):
        return IntentRoute(
            runtime={"kind": "react"},
            executor={"kind": "single_agent"},
            main_agent_instruction="",
            trace_payload={},
        )

    monkeypatch.setattr("src.server.routes.chat.run_context.get_registered_agent", lambda agent_id: agent_config)
    monkeypatch.setattr("src.server.routes.chat.run_context.get_effective_model_settings", lambda: SimpleNamespace(
        default_model="model",
        fallback_models=[],
    ))
    monkeypatch.setattr("src.server.routes.chat.run_context.check_message_readiness_with_model", fake_readiness)
    monkeypatch.setattr("src.server.routes.chat.run_context.route_chat_message_with_model", fake_route)
    monkeypatch.setattr("src.server.routes.chat.run_context.get_active_session_skill_run", lambda *args: None)
    monkeypatch.setattr("src.server.routes.chat.runtime_governance.build_habit_prompt", lambda *args, **kwargs: "")
    monkeypatch.setattr("src.server.routes.chat.runtime_governance.build_professional_prompt", lambda *args, **kwargs: "")
    monkeypatch.setattr(
        "src.server.routes.chat.run_context.build_runtime_skills",
        lambda *, skill_ids, **kwargs: captured.update(skill_ids=skill_ids) or [],
    )
    monkeypatch.setattr(
        "src.server.routes.chat.run_context._build_runtime_mcp_servers",
        lambda agent, *, allowed_server_ids: captured.update(mcp_ids=allowed_server_ids) or [],
    )
    monkeypatch.setattr(
        "src.server.routes.chat.run_context._build_runtime_knowledge_tools",
        lambda agent, *, allowed_tool_ids: captured.update(http_ids=allowed_tool_ids) or [],
    )

    runtime_context = resolve_runtime_context(
        project_id=project["id"],
        session_id="session-chat-resources",
        run_id="run-chat-resources",
        agent_id="agent-chat",
    )
    ctx = asyncio.run(build_chat_run_context(
        ChatMessage(
            session_id="session-chat-resources",
            project_id=project["id"],
            message="执行任务",
            agent_id="agent-chat",
        ),
        runtime_context,
    ))

    assert captured["readiness_skill_ids"] == ["skill-allowed"]
    assert captured["skill_ids"] == ["skill-allowed"]
    assert captured["mcp_ids"] == frozenset({"mcp-allowed"})
    assert captured["http_ids"] == frozenset({"http-allowed"})
    assert ctx.project_resources and ctx.project_resources.project_id == project["id"]


class _NoopVectorBackend:
    """测试用向量后端，避免引入真实索引。"""

    def ensure_collection(self, config):
        return None

    def upsert(self, collection, documents):
        return None

    def delete(self, collection, ids):
        return None

    def query(self, query):
        return []
