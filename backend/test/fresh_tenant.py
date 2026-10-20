"""全新 tenant 夾具。不預置 Workspace / Project，並覆蓋 autouse local tenant 的資料庫。"""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.catalog.agent_models import AgentResponse
from src.catalog.http_files import HttpToolFileRecord, dump_http_yaml
from src.catalog.loader import invalidate_catalog, save_agent
from src.catalog.mcp_files import McpFileRecord, dump_mcp_yaml
from src.config import runtime_paths
from src.config.runtime_paths import global_mcp_root, global_tools_root
from src.skills.catalog import current_skill_context
from src.skills.policy import set_skill_policy
from src.skills.service import catalog_for
from src.storage import database


class ModelBoundaryReached(Exception):
    """驗收用例到達模型工廠即停止。"""

    def __init__(self, kwargs: dict) -> None:
        super().__init__("model boundary")
        self.kwargs = kwargs


def _assert_no_scope_rows() -> None:
    database.ensure_db()
    with database._lock, database._connect() as conn:
        workspaces = conn.execute("SELECT COUNT(*) FROM workspaces").fetchone()[0]
        projects = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
    assert workspaces == 0
    assert projects == 0


@pytest.fixture()
def fresh_tenant(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "fresh-tenant"
    root.mkdir()
    monkeypatch.setattr(runtime_paths, "USER_DATA_ROOT", root)
    monkeypatch.setattr(runtime_paths, "USING_CUSTOM_DATA_ROOT", True)
    runtime_paths._ensured_tenants.clear()
    db_path = root / "fresh.db"
    monkeypatch.setattr(
        "src.config.settings.settings.database_url",
        "sqlite+aiosqlite:///" + str(db_path),
    )
    database.forget_all()
    runtime_paths.ensure_tenant_directories()
    database.ensure_db()
    _assert_no_scope_rows()
    yield root
    _assert_no_scope_rows()
    database.forget_all()


def install_skill(name: str, *, shared: bool) -> str:
    package = runtime_paths.tenant_root() / "skills" / name
    package.mkdir(parents=True, exist_ok=True)
    (package / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name}\n---\n{name} 正文\n",
        encoding="utf-8",
    )
    item = next(candidate for candidate in catalog_for(refresh=True).candidates if candidate.name == name)
    if not shared:
        set_skill_policy(current_skill_context().policy_path, item.id, availableToAllAgents=False)
    return item.id


def register_agent(agent_id: str, *, skills=(), mcp=(), http=()) -> None:
    invalidate_catalog()
    save_agent(AgentResponse.model_validate({
        "id": agent_id,
        "name": agent_id,
        "avatar": "",
        "description": "desc",
        "system_prompt": "prompt",
        "modelConfig": {"maxReactIters": 8},
        "mcpTools": list(mcp),
        "knowledgeTools": list(http),
        "skills": list(skills),
        "tags": [],
        "guardrails": {},
        "status": "idle",
        "created_at": "2026-10-19T00:00:00+00:00",
        "updated_at": "2026-10-19T00:00:00+00:00",
        "role": "chat",
        "jobToolkit": {},
    }))


def write_mcp(server_id: str) -> None:
    root = global_mcp_root()
    root.mkdir(parents=True, exist_ok=True)
    record = McpFileRecord(id=server_id, name=server_id, transport="stdio", command="true", enabled=True)
    (root / f"{server_id}.yaml").write_text(dump_mcp_yaml(record), encoding="utf-8")


def write_http_tool(tool_id: str) -> None:
    root = global_tools_root()
    root.mkdir(parents=True, exist_ok=True)
    record = HttpToolFileRecord(id=tool_id, name=tool_id, url="https://example.test/tool", enabled=True)
    (root / f"{tool_id}.yaml").write_text(dump_http_yaml(record), encoding="utf-8")


def capture_model_boundary(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    calls: list[dict] = []

    def fake(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        raise ModelBoundaryReached(kwargs)

    monkeypatch.setattr("src.runtime.agent_spec.get_or_create_agent", fake)
    monkeypatch.setattr("src.subagents.runner.get_or_create_agent", fake)
    return calls


def fake_model_binding(monkeypatch: pytest.MonkeyPatch) -> None:
    binding = SimpleNamespace(
        primary=SimpleNamespace(
            entry=SimpleNamespace(
                id="deepseek-v4-flash",
                capabilities=SimpleNamespace(images=False),
                parameters=SimpleNamespace(model_dump=lambda **_kwargs: {}),
            ),
            provider="deepseek",
            provider_name="DeepSeek",
            adapter="deepseek",
            api_key="test-only",
            base_url="http://127.0.0.1:1",
            default_model="deepseek-v4-flash",
        ),
        chain=[],
        catalog=SimpleNamespace(revision=1),
    )

    def resolve(*_args, **_kwargs):
        return binding

    monkeypatch.setattr("src.config.runtime_settings.resolve_model_binding", resolve)
    monkeypatch.setattr("src.server.routes.chat.handlers.send.resolve_model_binding", resolve)


def wait_run(run_id: str, timeout: float = 5.0):
    from src.server.routes.chat.background_runs import _runs

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = _runs.get(run_id)
        if run is not None and run.status in {"completed", "failed", "cancelled"}:
            return run
        time.sleep(0.05)
    raise AssertionError(f"Run 未結束: {run_id}")
