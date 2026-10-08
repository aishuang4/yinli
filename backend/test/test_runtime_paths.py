"""桌面端运行时可写路径回归测试。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def test_desktop_writable_paths_share_user_data_root(tmp_path: Path) -> None:
    """Electron 注入 userData 后，所有应用写入路径都必须位于该目录。"""

    user_data_root = tmp_path / "agent-workstation-desktop"
    env = {
        **os.environ,
        "AGENT_WORKSTATION_DATA_DIR": str(user_data_root),
        "AGENT_WORKSTATION_DATABASE_URL": "sqlite+aiosqlite:///./data/agent_workstation.db",
    }
    script = """
import json
from src.agents.runtime_types import WORKSPACE_DIR
from src.config import runtime_paths
from src.config.secret_store import SECRET_DIR
from src.memory.markdown_store import MarkdownMemoryStore
from src.server.routes import agents, mcp, skills
from src.server.routes.chat.models import CHAT_DATA_PATH
from src.server.routes.chat.team_runtime.store import TEAM_RUNTIME_ROOT
from src.storage.database import DB_PATH
from src.subagents.models import SUBAGENT_USER_DIR
from src.vector_store.providers.zvec import ZvecVectorStoreBackend

print(json.dumps({
    "database": str(DB_PATH),
    "memory": str(MarkdownMemoryStore().root),
    "vector": str(ZvecVectorStoreBackend._collection_path("memory_nodes")),
    "team": str(TEAM_RUNTIME_ROOT),
    "skills": str(skills.SKILL_PACKAGES_DIR),
    "subagents": str(SUBAGENT_USER_DIR),
    "sandboxes": str(runtime_paths.SANDBOX_ROOT),
    "secrets": str(SECRET_DIR),
    "legacy_agents": str(agents.AGENTS_REGISTRY_FILE),
    "legacy_mcp": str(mcp.USER_MCP_REGISTRY_FILE),
    "legacy_chat": str(CHAT_DATA_PATH),
    "workspace": str(WORKSPACE_DIR),
    "logs": str(runtime_paths.LOG_ROOT),
    "temp": str(runtime_paths.TEMP_ROOT),
}))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    paths = json.loads(result.stdout.strip().splitlines()[-1])

    assert paths
    for path in paths.values():
        assert Path(path).is_relative_to(user_data_root), path
    assert Path(paths["subagents"]).parent == user_data_root


def test_legacy_subagents_migrate_to_top_level_root(tmp_path: Path, monkeypatch) -> None:
    """旧 data/subagents 内容迁移到新的 userData/subagents，且不覆盖现有定义。"""

    from src.config import runtime_paths

    legacy_root = tmp_path / "data" / "subagents"
    target_root = tmp_path / "subagents"
    legacy_root.mkdir(parents=True)
    (legacy_root / "reviewer.md").write_text("legacy", encoding="utf-8")
    (legacy_root / "shared.md").write_text("legacy shared", encoding="utf-8")
    target_root.mkdir()
    (target_root / "shared.md").write_text("current", encoding="utf-8")

    monkeypatch.setattr(runtime_paths, "LEGACY_SUBAGENT_ROOT", legacy_root)
    monkeypatch.setattr(runtime_paths, "SUBAGENT_ROOT", target_root)
    runtime_paths._migrate_legacy_subagents()

    assert (target_root / "reviewer.md").read_text(encoding="utf-8") == "legacy"
    assert (target_root / "shared.md").read_text(encoding="utf-8") == "current"
    assert (legacy_root / "shared.md").exists()


def test_ephemeral_desktop_defaults_migrate_to_stable_workspace(tmp_path, monkeypatch) -> None:
    """历史打包版写入的临时路径会迁移，用户自定义外部路径不受影响。"""

    from src.storage import database

    db_path = tmp_path / "data" / "agent_workstation.db"
    workspace_root = tmp_path / "workspace"
    monkeypatch.setattr(database, "DB_PATH", db_path)
    monkeypatch.setattr(database, "PROJECT_ROOT", workspace_root)
    monkeypatch.setattr(database, "USING_CUSTOM_DATA_ROOT", True)
    monkeypatch.setattr(database, "_initialized", False)
    database.ensure_db()

    with database._connect() as conn:
        conn.execute(
            "UPDATE projects SET root_path = ? WHERE id = ?",
            ("/private/tmp/_MEIold/runtime", database.DEFAULT_PROJECT_ID),
        )
        conn.commit()

    monkeypatch.setattr(database, "_initialized", False)
    database.ensure_db()

    assert database.get_project(database.DEFAULT_PROJECT_ID)["rootPath"] == str(workspace_root)
    normalized = database._normalize_guardrails({
        "filesystem_scope": "/private/tmp/_MEIold/runtime",
    })
    assert normalized["filesystem_scope"] == str(workspace_root)

    with database._connect() as conn:
        conn.execute(
            "UPDATE projects SET root_path = ? WHERE id = ?",
            ("/private/var/folders/example/T/agent-workstation", database.DEFAULT_PROJECT_ID),
        )
        conn.commit()

    monkeypatch.setattr(database, "_initialized", False)
    database.ensure_db()

    assert database.get_project(database.DEFAULT_PROJECT_ID)["rootPath"] == str(workspace_root)
    custom_root = tmp_path / "external-project"
    normalized_custom = database._normalize_guardrails({
        "filesystem_scope": str(custom_root),
    })
    assert normalized_custom["filesystem_scope"] == str(custom_root)
