"""sqlite agents 表一次性迁到 profile 文件。"""

from src.catalog.loader import get_registered_agent, invalidate_catalog
from src.catalog.migrate_agents import ensure_agent_file_catalog, is_agent_catalog_migrated
from src.catalog.paths import profile_path
from src.storage import database


def test_migrate_exports_then_clears_sqlite(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("src.config.runtime_paths.USER_DATA_ROOT", tmp_path)
    from src.storage.database import get_json_record, upsert_json_record

    upsert_json_record("catalog", "agent_migration_v1", {"status": "pending"})
    database.upsert_agent({
        "id": "existing-agent",
        "name": "Existing",
        "system_prompt": "keep-me",
        "workspaceId": "default",
        "guardrails": {},
        "skills": [],
        "mcpTools": [],
        "knowledgeTools": [],
        "tags": [],
        "role": "chat",
    })
    # 上面 upsert 可能因 marker 未 done 只写 sqlite
    upsert_json_record("catalog", "agent_migration_v1", {"status": ""})
    invalidate_catalog()
    ensure_agent_file_catalog()
    assert is_agent_catalog_migrated()
    assert get_registered_agent("existing-agent") is not None
    assert get_registered_agent("existing-agent").system_prompt == "keep-me"
    assert profile_path("default", "existing-agent").is_file()
    assert all(row["id"] != "existing-agent" for row in database.list_agents())
    marker = get_json_record("catalog", "agent_migration_v1")
    assert marker["status"] == "done"


def test_upsert_after_migrate_does_not_write_sqlite(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("src.config.runtime_paths.USER_DATA_ROOT", tmp_path)
    invalidate_catalog()
    ensure_agent_file_catalog()
    assert is_agent_catalog_migrated()
    database.upsert_agent({
        "id": "after-migrate",
        "name": "After",
        "system_prompt": "from-catalog",
        "workspaceId": "default",
        "guardrails": {},
        "skills": [],
        "mcpTools": [],
        "knowledgeTools": [],
        "tags": [],
        "role": "chat",
    })
    assert all(row["id"] != "after-migrate" for row in database.list_agents())
    loaded = get_registered_agent("after-migrate")
    assert loaded is not None
    assert loaded.system_prompt == "from-catalog"
