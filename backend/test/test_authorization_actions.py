"""真实受控工具的规范化、目标版本和受限参数隔离回归。"""

import json

import pytest

from src.agents.guarded_bash import GuardedBash
from src.security.authorization.actions import RuntimeActionBuilder
from src.tools.configured_http_tool import ConfiguredHttpTool, prepare_configured_http_request
from src.tools.scoped_read import ScopedRead
from src.tools.scoped_write import ScopedWrite
from src.tenancy.context import TenantContext, use_tenant
from test.authorization_fakes import run_async
from test.test_permission_records import action_for


@run_async
async def test_relative_and_absolute_file_action_match_and_versions_revoke(tmp_path):
    file = tmp_path / "report.md"
    file.write_text("first")
    builder = RuntimeActionBuilder(action_for().authorization)
    tool = ScopedWrite(base_dir=str(tmp_path), scope_dir=str(tmp_path))
    action = await builder(tool, "call", '{"file_path":"report.md","content":"private body"}')
    absolute = await builder(tool, "call", json.dumps({"content": "private body", "file_path": str(file)}))
    assert action.fingerprint == absolute.fingerprint
    assert action.targets[0].target == str(file)
    assert "private body" not in action.model_dump_json()
    assert json.loads(builder.execution_input(action))["content"] == "private body"
    assert await builder.revalidate(action) is None
    file.write_text("changed outside agent")
    assert await builder.revalidate(action)


@run_async
async def test_file_symlink_and_missing_target_changes_are_rejected(tmp_path):
    root = tmp_path / "scope"
    root.mkdir()
    tool = ScopedWrite(base_dir=str(root), scope_dir=str(root))
    builder = RuntimeActionBuilder(action_for().authorization)
    action = await builder(tool, "c", '{"file_path":"new.md","content":"next"}')
    assert action.targets[0].version == "absent"
    (root / "new.md").write_text("other actor created")
    assert await builder.revalidate(action)
    (root / "escape").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError):
        await builder(tool, "c", '{"file_path":"escape/outside","content":"x"}')


@run_async
async def test_read_resolves_project_fallback_before_snapshot(tmp_path):
    session = tmp_path / "session"
    session.mkdir()
    (tmp_path / "existing.md").write_text("old")
    builder = RuntimeActionBuilder(action_for().authorization)
    action = await builder(ScopedRead(base_dir=str(session), scope_dir=str(tmp_path)), "c",
                           '{"file_path":"existing.md"}')
    assert action.targets[0].target == str(tmp_path / "existing.md")
    assert action.effect == "read_only"


@run_async
async def test_credentials_and_outbound_body_do_not_enter_snapshot_and_revocation_changes_identity():
    builder = RuntimeActionBuilder(action_for().authorization)
    tool = ConfiguredHttpTool({"id": "mail", "name": "mail", "method": "POST",
        "url": "https://example.org/send/{{recipient}}", "authType": "bearer",
        "authConfig": {"token": "private-key"}, "bodyTemplate": {"text": "{{text}}"}})
    raw = '{"recipient":"bob","text":"private-content"}'
    action = await builder(tool, "c", raw)
    saved = action.model_dump_json()
    assert "private-key" not in saved and "private-content" not in saved
    assert action.targets[0].target == "https://example.org/send/bob"
    assert action.effect == "outbound"
    prepared = prepare_configured_http_request(tool.config, json.loads(raw))
    assert prepared["headers"]["Authorization"] == "Bearer private-key"
    tool.config["authConfig"]["token"] = "rotated-key"
    assert await builder.revalidate(action)


@run_async
async def test_restricted_input_cannot_be_read_by_other_tenant_or_after_losing_scene():
    builder = RuntimeActionBuilder(action_for().authorization)
    tool = ConfiguredHttpTool({"name": "test", "url": "https://example.org"})
    action = await builder(tool, "c", '{"password":"private"}')
    assert "private" not in action.model_dump_json()
    with use_tenant(TenantContext.hosted("usr_" + "a" * 32)):
        with pytest.raises(ValueError):
            builder.execution_input(action)
    builder.vault.clear()
    with pytest.raises(ValueError):
        builder.execution_input(action)


@run_async
async def test_explicit_http_deny_is_enforced_in_prepared_execution():
    builder = RuntimeActionBuilder(action_for().authorization)
    tool = ConfiguredHttpTool({"name": "blocked", "url": "https://example.org", "approvalPolicy": "deny"})
    with pytest.raises(ValueError):
        await builder(tool, "c", '{}')
    with pytest.raises(ValueError):
        prepare_configured_http_request(tool.config, {})


@run_async
async def test_shell_template_requires_scoped_target_and_fixed_executable(tmp_path):
    (tmp_path / "report.txt").write_text("report")
    builder = RuntimeActionBuilder(action_for().authorization, filesystem_scope=str(tmp_path))
    tool = GuardedBash([], timeout_seconds=3, cwd=str(tmp_path))
    action = await builder(tool, "c", '{"command":"cat report.txt"}')
    assert action.effect == "read_only"
    assert json.loads(builder.execution_input(action))["command"].startswith("/bin/cat ")
    assert await builder.revalidate(action) is None
    with pytest.raises(ValueError):
        await builder(tool, "outside", '{"command":"cat /etc/passwd"}')
    script = await builder(tool, "script", '{"command":"python script.py"}')
    assert script.effect == "unknown"


@run_async
async def test_known_webpage_redirect_does_not_exempt_new_query_target():
    import httpx
    from src.security.authorization.execution_context import execution_scope
    from src.tools.web_fetch import WebFetch
    seen = []
    url = "https://example.org/page"
    async def resolver(host, port):
        return ("93.184.216.34",)
    async def handler(request):
        seen.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://evil.example/upload?data=private"})
    tool = WebFetch(transport=httpx.MockTransport(handler), resolver=resolver)
    builder = RuntimeActionBuilder(action_for().authorization, known_urls=frozenset({url}))
    action = await builder(tool, "c", json.dumps({"url": url}))
    assert action.effect == "read_only"
    with execution_scope(action):
        result = await tool(**json.loads(builder.execution_input(action)))
    assert result.state == "error" and seen == [url]


@run_async
async def test_symlink_retarget_revokes_original_relative_approval(tmp_path):
    (tmp_path / "first").write_text("first")
    (tmp_path / "second").write_text("second")
    link = tmp_path / "current"
    link.symlink_to(tmp_path / "first")
    builder = RuntimeActionBuilder(action_for().authorization)
    tool = ScopedWrite(base_dir=str(tmp_path), scope_dir=str(tmp_path))
    action = await builder(tool, "call", '{"file_path":"current","content":"approved"}')
    # SDK 使用 canonical realpath 重建也不能抹掉原路径表达式。
    await builder(tool, "call", builder.execution_input(action))
    link.unlink()
    link.symlink_to(tmp_path / "second")
    assert await builder.revalidate(action)


@run_async
async def test_generic_http_and_knowledge_bind_final_targets_and_merged_credentials():
    from src.tools.http_request import HttpRequest
    from src.tools.knowledge_base_search import KnowledgeBaseSearch
    builder = RuntimeActionBuilder(action_for().authorization)
    generic = await builder(HttpRequest(), "http", json.dumps({
        "url": "https://example.org/api", "query": {"secret": "private-key"},
        "headers": {"Authorization": "private-key"},
    }))
    assert generic.targets[0].target == "https://example.org/api"
    assert generic.effect == "outbound" and "private-key" not in generic.model_dump_json()
    kb = KnowledgeBaseSearch("kb", "kb", "search", "https://example.org/search",
                             authorization="private-key", default_payload={"dataset": "first"})
    action = await builder(kb, "kb", '{"query":"private-search"}')
    assert "private-search" not in action.model_dump_json()
    kb.default_payload["dataset"] = "second"
    assert await builder.revalidate(action)


@run_async
async def test_native_mcp_identity_and_connection_revocation_are_bound():
    from contextlib import asynccontextmanager
    from agentscope.tool import MCPTool
    from mcp.types import Tool
    @asynccontextmanager
    async def no_connection():
        raise AssertionError("构造快照不得连接 MCP")
        yield
    tool = MCPTool("server", Tool(name="send.mail", inputSchema={"type": "object"}), client_gen=no_connection)
    connections = {"server": {"url": "https://example.org/mcp", "headers": {"Authorization": "private-key"}}}
    builder = RuntimeActionBuilder(action_for().authorization, mcp_connections=connections)
    with pytest.raises(ValueError):
        await builder(tool, "c", '{}')
    builder.register_tool_identity(tool, lambda: {"server": tool.mcp_name, "tool": tool._tool.model_dump(mode="json")})
    action = await builder(tool, "c", '{"arbitrary":"private-key"}')
    assert "private-key" not in action.model_dump_json()
    assert action.effect == "unknown" and action.targets[0].target == "https://example.org/mcp"
    connections.clear()
    assert await builder.revalidate(action)
