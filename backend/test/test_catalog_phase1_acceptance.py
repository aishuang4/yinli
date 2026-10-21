"""docs/file-catalog-v1.md §19 第一期验收。"""

from __future__ import annotations

import asyncio

import pytest

from src.agents.factory import _create_default_toolkit
from src.agents.runtime_types import RuntimeToolScope
from src.catalog.index import refresh_project_index
from src.catalog.loader import catalog_fingerprint, invalidate_catalog
from src.catalog.migrate_agents import ensure_agent_file_catalog
from src.catalog.paths import extra_filesystem_scopes_for_workspace, profile_path
from src.server.routes import agents as agents_routes
from src.server.routes.chat.models import ChatMessage
from src.server.routes.chat.run_context import resolve_agent_system_prompt
from src.server.routes.chat.runtime_builder import build_runtime_agent_cache_key
from src.storage import database
from src.tools.scoped_edit import ScopedEdit
from src.tools.scoped_paths import resolve_workspace_path
from src.tools.scoped_read import ScopedRead


@pytest.fixture()
def phase1_root(tmp_path, monkeypatch):
    monkeypatch.setattr("src.config.runtime_paths.USER_DATA_ROOT", tmp_path)
    invalidate_catalog()
    ensure_agent_file_catalog()
    agents_routes._sync_agents_from_catalog()
    yield tmp_path
    database.forget_db(database.db_path())
    invalidate_catalog()


def _create_user_agent(prompt: str = "第一期提示词") -> agents_routes.AgentResponse:
    return asyncio.run(agents_routes.create_agent(agents_routes.AgentCreate(
        name="验收助手",
        description="phase1",
        system_prompt=prompt,
        
    )))


async def _tool_names() -> list[str]:
    schemas = await _create_default_toolkit().get_tool_schemas()
    return [
        item.get("function", {}).get("name") or item.get("name") or ""
        for item in schemas
    ]


def test_phase1_sqlite_empty_file_exists_and_page_save_writes_file(phase1_root) -> None:
    """§19.1–3：重启后表空、磁盘有 profile、页面保存改文件不写 sqlite。"""

    created = _create_user_agent()
    path = profile_path("default", created.id)
    assert path.is_file()
    assert "第一期提示词" in path.read_text(encoding="utf-8")
    assert all(row["id"] != created.id for row in database.list_agents())

    listed = asyncio.run(agents_routes.list_agents())
    assert any(item.id == created.id for item in listed)

    asyncio.run(agents_routes.update_agent(
        created.id,
        agents_routes.AgentUpdate(system_prompt="页面保存后的提示词"),
    ))
    assert "页面保存后的提示词" in path.read_text(encoding="utf-8")
    assert all(row["id"] != created.id for row in database.list_agents())


def test_phase1_chat_reads_index_and_edit_changes_next_round(phase1_root) -> None:
    """§19.4：INDEX 含 path；extra_scopes 可 Read/Edit；改文件后下一轮 prompt 与 cache key 变。"""

    created = _create_user_agent("原始提示词")
    project = phase1_root / "project"
    project.mkdir()
    index = refresh_project_index(project, project_id="default")
    assert index is not None
    index_text = index.read_text(encoding="utf-8")
    path = profile_path("default", created.id)
    assert str(path.resolve()) in index_text
    assert created.id in index_text

    extras = extra_filesystem_scopes_for_workspace("default")
    resolved = resolve_workspace_path(
        str(path),
        base_dir=str(project),
        scope_dir=str(project),
        extra_scopes=extras,
    )
    assert resolved == str(path.resolve())

    read_chunk = asyncio.run(ScopedRead(
        base_dir=str(project),
        scope_dir=str(project),
        extra_scopes=extras,
    )(file_path=str(path)))
    assert getattr(read_chunk, "state", None) != "error"
    assert "原始提示词" in str(getattr(read_chunk, "content", read_chunk))

    before = agents_routes.get_registered_agent(created.id)
    assert before is not None
    assert before.system_prompt == "原始提示词"
    before_key = build_runtime_agent_cache_key(
        agent_id=created.id,
        session_id="session-1",
        system_prompt=before.system_prompt,
        model_name="deepseek-v4-flash",
        deep_thinking=False,
        fallback_names=[],
        used_skills=[],
        mcp_servers=[],
        knowledge_tools=[],
        runtime_tool_scope=RuntimeToolScope(),
        guardrail_cache_key="g",
    )
    before_fp = catalog_fingerprint()

    edit_chunk = asyncio.run(ScopedEdit(
        base_dir=str(project),
        scope_dir=str(project),
        extra_scopes=extras,
    )(
        file_path=str(path),
        old_string="原始提示词",
        new_string="下一轮新提示词",
    ))
    assert getattr(edit_chunk, "state", None) != "error"
    invalidate_catalog()

    after = agents_routes.get_registered_agent(created.id)
    assert after is not None
    assert after.system_prompt == "下一轮新提示词"
    after_key = build_runtime_agent_cache_key(
        agent_id=created.id,
        session_id="session-1",
        system_prompt=after.system_prompt,
        model_name="deepseek-v4-flash",
        deep_thinking=False,
        fallback_names=[],
        used_skills=[],
        mcp_servers=[],
        knowledge_tools=[],
        runtime_tool_scope=RuntimeToolScope(),
        guardrail_cache_key="g",
    )
    assert catalog_fingerprint() != before_fp
    assert after_key != before_key
    stale_request = ChatMessage(
        message="你好",
        agent_id=created.id,
        system_prompt="你是验收助手。每次回答第一句必须是：【旧提示词生效】",
    )
    assert resolve_agent_system_prompt(after, stale_request) == "下一轮新提示词"


def test_phase1_no_system_crud_tools_and_secret_write_denied(phase1_root) -> None:
    """§19.5 / §19.8：没有系统 CRUD 工具；models.json 与密钥目录 Write 被拒。"""

    names = asyncio.run(_tool_names())
    forbidden = {
        "ListAgents", "GetAgentConfig", "UpdateAgent", "ReloadCatalog",
        "ListProjectCapabilities", "ScheduleJob", "PauseJob", "ListJobs", "ConfirmJob",
    }
    assert forbidden.isdisjoint(set(names))

    project = phase1_root / "project"
    project.mkdir()
    extras = extra_filesystem_scopes_for_workspace("default")
    models = phase1_root / "models.json"
    models.write_text("{}", encoding="utf-8")
    secrets = phase1_root / "data" / "secrets" / "catalog" / "token"
    secrets.parent.mkdir(parents=True)
    secrets.write_text("secret", encoding="utf-8")
    with pytest.raises(ValueError, match="outside filesystem scope"):
        resolve_workspace_path(
            str(models),
            base_dir=str(project),
            scope_dir=str(project),
            extra_scopes=extras,
        )
    with pytest.raises(ValueError, match="outside filesystem scope"):
        resolve_workspace_path(
            str(secrets),
            base_dir=str(project),
            scope_dir=str(project),
            extra_scopes=extras,
        )


def test_phase1_system_agent_protected_and_cross_workspace(phase1_root) -> None:
    """§19.6：系统 Agent 不可删，任意 Workspace 可见。"""

    with pytest.raises(Exception) as exc:
        asyncio.run(agents_routes.delete_agent("default"))
    assert getattr(exc.value, "status_code", None) == 403
    listed = asyncio.run(agents_routes.list_agents())
    ids = [item.id for item in listed]
    assert "default" in ids
    assert "system-websearch-job" in ids
