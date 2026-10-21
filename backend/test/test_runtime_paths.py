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
from src.config import runtime_paths
from src.config.model_catalog import models_path
from src.config.secret_store import secret_dir
from src.memory.markdown_store import MarkdownMemoryStore
from src.server.routes.chat.models import chat_data_path
from src.storage.database import db_path
from src.vector_store.providers.zvec import ZvecVectorStoreBackend

print(json.dumps({
    "database": str(db_path()),
    "models": str(models_path()),
    "memory": str(MarkdownMemoryStore().root),
    "vector": str(ZvecVectorStoreBackend._collection_path("memory_nodes")),
    "team": str(runtime_paths.team_runtime_root()),
    "skills": str(runtime_paths.skill_packages_root()),
    "subagents": str(runtime_paths.subagent_root()),
    "sandboxes": str(runtime_paths.sandbox_root()),
    "secrets": str(secret_dir()),
    "legacy_chat": str(chat_data_path()),
    "workspace": str(runtime_paths.project_default_root()),
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


def test_ensure_tenant_directories_does_not_move_old_data_dirs(tmp_path) -> None:
    """启动只建新目录，不把 data/subagents 或 data/skill_packages 搬进租户根。"""

    from src.config import runtime_paths
    from src.tenancy.context import TenantContext, use_tenant

    tenant_id = "phase5-paths"
    runtime_paths.forget_tenant_directories(tenant_id)
    with use_tenant(TenantContext(tenant_id=tenant_id, data_root=tmp_path, kind="local")):
        legacy_agents = runtime_paths.data_root() / "subagents"
        legacy_skills = runtime_paths.data_root() / "skill_packages"
        legacy_agents.mkdir(parents=True)
        legacy_skills.mkdir(parents=True)
        (legacy_agents / "reviewer.md").write_text("legacy", encoding="utf-8")
        (legacy_skills / "old-skill").mkdir()

        runtime_paths.ensure_tenant_directories()

        assert (legacy_agents / "reviewer.md").read_text(encoding="utf-8") == "legacy"
        assert (legacy_skills / "old-skill").is_dir()
        assert not (runtime_paths.subagent_root() / "reviewer.md").exists()
        assert not (runtime_paths.skill_packages_root() / "old-skill").exists()
    runtime_paths.forget_tenant_directories(tenant_id)


def test_ephemeral_desktop_defaults_migrate_to_stable_workspace(monkeypatch) -> None:
    """历史打包版写入的临时路径会迁移，用户自定义外部路径不受影响。"""

    from src.config import runtime_paths
    from src.storage import database

    monkeypatch.setattr(runtime_paths, "USING_CUSTOM_DATA_ROOT", True)
    stable_root = runtime_paths.project_default_root(database.DEFAULT_PROJECT_ID)

    database.ensure_db()
    with database._connect() as conn:
        conn.execute(
            "UPDATE projects SET primary_path = ? WHERE slug = ?",
            ("/private/tmp/_MEIold/runtime", database.DEFAULT_PROJECT_ID),
        )
        conn.commit()

    database.forget_db(database.db_path())
    database.ensure_db()

    assert database.get_project(database.DEFAULT_PROJECT_ID)["primaryPath"] == str(stable_root.resolve())
    normalized = database._normalize_guardrails({
        "filesystem_scope": "/private/tmp/_MEIold/runtime",
    })
    assert normalized["filesystem_scope"] == str(stable_root)

    with database._connect() as conn:
        conn.execute(
            "UPDATE projects SET primary_path = ? WHERE slug = ?",
            ("/private/var/folders/example/T/agent-workstation", database.DEFAULT_PROJECT_ID),
        )
        conn.commit()

    database.forget_db(database.db_path())
    database.ensure_db()

    assert database.get_project(database.DEFAULT_PROJECT_ID)["primaryPath"] == str(stable_root.resolve())
    custom_root = runtime_paths.USER_DATA_ROOT / "external-project"
    normalized_custom = database._normalize_guardrails({
        "filesystem_scope": str(custom_root),
    })
    assert normalized_custom["filesystem_scope"] == str(custom_root)
