"""托管执行：stdio MCP、沙箱改写、容器名隔离、脚本走沙箱。"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from src.agents.runtime_types import AgentGuardrails
from src.sandbox.hosted_workspace import open_hosted_workspace
from src.sandbox.provider import get_sandbox_provider
from src.scheduler.script_job import execute_script_in_dir
from src.server.routes.mcp import _reject_hosted_mcp
from src.tenancy.capabilities import coerce_hosted_guardrails
from src.tenancy.context import TenantContext, use_tenant
from src.config.runtime_paths import tenant_root

pytest_plugins = ["test.hosted_support"]

USER_A = "usr_" + "a" * 32
USER_B = "usr_" + "b" * 32


def test_hosted_rejects_stdio_mcp(hosted_env):
    with use_tenant(TenantContext.hosted(USER_A)):
        with pytest.raises(HTTPException) as exc:
            _reject_hosted_mcp(transport="stdio", url="")
        assert exc.value.status_code == 403
        assert exc.value.detail == "Web 端不支持本地 MCP"


def test_saving_local_backend_is_rewritten_to_docker(hosted_env):
    with use_tenant(TenantContext.hosted(USER_A)):
        guardrails = coerce_hosted_guardrails(AgentGuardrails(execution_backend="local"))
    assert guardrails.execution_backend == "docker"
    assert guardrails.persistent_sandbox is True


def test_fake_sandbox_names_include_tenant_and_drop_caps(hosted_env):
    fake = get_sandbox_provider()
    for user, project in ((USER_A, "proj-a"), (USER_B, "proj-b")):
        with use_tenant(TenantContext.hosted(user)):
            root = tenant_root() / "projects" / project
            root.mkdir(parents=True)
            open_hosted_workspace(
                tenant_id=user,
                project_slug=project,
                agent_id="default",
                fallback_root=root,
                base_image="python:3.11-slim",
            )
    assert len(fake.created) == 2
    assert fake.created[0].name != fake.created[1].name
    for spec in fake.created:
        assert spec.name.startswith("y")
        assert len(spec.name) == 25
        assert spec.host_config["CapDrop"] == ["ALL"]
        assert spec.binds[0].endswith(":/workspace:rw")
        assert spec.labels["yinli.tenant"] in {USER_A, USER_B}
        assert spec.labels["yinli.project"] in {"proj-a", "proj-b"}
        assert "/projects/" in spec.binds[0]


def test_script_job_uses_sandbox(hosted_env):
    fake = get_sandbox_provider()

    async def run() -> None:
        with use_tenant(TenantContext.hosted(USER_A)):
            cwd = tenant_root() / "firings" / "one"
            result = await execute_script_in_dir(
                source="print('hi')\n",
                filename="job.py",
                interpreter="python3",
                args=[],
                timeout_seconds=5,
                cwd=cwd,
            )
        assert result.stdout == "sandbox-ok"
        assert fake.exec_log

    asyncio.run(run())
