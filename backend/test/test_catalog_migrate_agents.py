"""用户 Agent 正文只进 profile 文件，不进 sqlite agents 表。"""

from src.storage.records.resource_catalog_records import list_agents

from src.catalog.loader import (
    get_registered_agent,
    import_sqlite_agent_record,
    invalidate_catalog,
)
from src.catalog.paths import profile_path
from src.storage.engine import database


def test_upsert_agent_writes_profile_not_sqlite(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("src.config.runtime_paths.USER_DATA_ROOT", tmp_path)
    invalidate_catalog()
    import_sqlite_agent_record({
        "id": "existing-agent",
        "name": "Existing",
        "system_prompt": "keep-me",
        "guardrails": {},
        "skills": [],
        "mcpTools": [],
        "knowledgeTools": [],
        "tags": [],
        "role": "chat",
    })
    loaded = get_registered_agent("existing-agent")
    assert loaded is not None
    assert loaded.system_prompt == "keep-me"
    assert profile_path("default", "existing-agent").is_file()
    assert all(row["id"] != "existing-agent" for row in list_agents())
