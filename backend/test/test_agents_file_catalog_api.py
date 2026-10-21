"""Agent HTTP 门面读写 profile 文件。"""

import asyncio

import pytest

from src.catalog.paths import profile_path, system_profile_path
from src.server.routes import agents as agents_routes


def test_create_update_delete_uses_files() -> None:
    created = asyncio.run(agents_routes.create_agent(agents_routes.AgentCreate(
        name="文件助手",
        description="d",
        system_prompt="hello-catalog",
        
    )))
    path = profile_path(created.id)
    assert path.is_file()
    assert "hello-catalog" in path.read_text(encoding="utf-8")

    updated = asyncio.run(agents_routes.update_agent(
        created.id,
        agents_routes.AgentUpdate(system_prompt="hello-again"),
    ))
    assert "hello-again" in path.read_text(encoding="utf-8")
    assert updated.system_prompt == "hello-again"

    asyncio.run(agents_routes.delete_agent(created.id))
    assert not path.exists()
    assert created.id not in agents_routes._agents


def test_system_agent_delete_forbidden() -> None:
    with pytest.raises(Exception) as exc:
        asyncio.run(agents_routes.delete_agent("default"))
    assert getattr(exc.value, "status_code", None) == 403
    asyncio.run(agents_routes.update_agent(
        "default",
        agents_routes.AgentUpdate(system_prompt="custom-default"),
    ))
    assert system_profile_path("default").is_file()
    assert "custom-default" in system_profile_path("default").read_text(encoding="utf-8")
