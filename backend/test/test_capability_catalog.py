"""能力库存、打标、认领与 settings 合并。"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.scheduler.capability_catalog import CapabilityInventoryItem
from src.scheduler.capability_match import (
    CHANNEL_INTENTS,
    SKILL_OK_INTENTS,
    claim_capabilities,
    extract_required_intents,
    tag_inventory_row,
)
from src.scheduler.mcp_runtime import mcp_injectable, resolve_mcp_runtime_servers
from src.scheduler.settings import (
    is_capability_gate_enabled,
    is_scheduled_jobs_enabled,
    set_scheduled_jobs_enabled,
    set_scheduled_jobs_settings,
)
from src.storage import database
from src.storage.database import ensure_db


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "agent_workstation.db")
    monkeypatch.setattr(database, "_initialized", False)
    yield
    database._initialized = False


def _row(**overrides) -> CapabilityInventoryItem:
    payload = {
        "capability_id": "mcp:gmail-send-email",
        "kind": "mcp",
        "resource_id": "gmail-send-email",
        "server_id": "gmail",
        "tool_name": "send_email",
        "title": "Send email",
        "description": "Send an email via Gmail",
        "tags": ["email.send"],
        "tag_source": {"email.send": "name"},
        "unattended_ok": True,
    }
    payload.update(overrides)
    return CapabilityInventoryItem.model_validate(payload)


def test_intent_closed_sets():
    assert "email.send" in CHANNEL_INTENTS
    assert "file.write" in SKILL_OK_INTENTS
    assert "email.send" not in SKILL_OK_INTENTS


def test_list_messages_is_email_read_not_send():
    tags, source = tag_inventory_row(
        kind="mcp",
        tool_name="list_messages",
        title="List messages",
        description="列出 Gmail 邮件",
    )
    assert "email.send" not in tags
    assert "email.read" in tags
    assert source.get("email.read") == "name"


def test_send_email_name_is_email_send():
    tags, source = tag_inventory_row(
        kind="mcp",
        tool_name="send_email",
        title="Send",
        description="",
    )
    assert "email.send" in tags
    assert source["email.send"] == "name"


def test_mail_word_in_description_does_not_make_send():
    tags, _source = tag_inventory_row(
        kind="mcp",
        tool_name="list_labels",
        title="Labels",
        description="gmail 邮件标签",
    )
    assert "email.send" not in tags


def test_http_request_builtin_is_not_email_send():
    tags, _source = tag_inventory_row(
        kind="builtin",
        tool_name="HttpRequest",
        title="HttpRequest",
        description="发送 HTTP 请求，也可以发邮件",
    )
    assert "email.send" not in tags
    assert "http.call" in tags


def test_claim_email_send_picks_send_not_list():
    inventory = [
        _row(
            capability_id="mcp:gmail-list-messages",
            resource_id="gmail-list-messages",
            tool_name="list_messages",
            tags=["email.read"],
            tag_source={"email.read": "name"},
        ),
        _row(),
    ]
    claimed, missing, blocked = claim_capabilities(["email.send"], inventory)
    assert missing == []
    assert blocked == []
    assert [item.capability_id for item in claimed if "email.send" in item.tags] == [
        "mcp:gmail-send-email",
    ]
    assert all(item.tool_name != "list_messages" for item in claimed)


def test_knowledge_row_cannot_satisfy_email_send():
    inventory = [
        _row(
            capability_id="knowledge:kb-1",
            kind="knowledge",
            resource_id="kb-1",
            tool_name="search",
            tags=["email.send", "knowledge.search"],
            tag_source={"email.send": "description", "knowledge.search": "declared"},
        ),
    ]
    claimed, missing, _blocked = claim_capabilities(["email.send"], inventory)
    assert claimed == []
    assert [item.intent for item in missing] == ["email.send"]


def test_skill_only_cannot_satisfy_email_send():
    inventory = [
        _row(
            capability_id="skill:mail-writer",
            kind="skill",
            resource_id="mail-writer",
            tool_name="mail-writer",
            tags=["email.send", "office.generate"],
            tag_source={"email.send": "declared"},
        ),
    ]
    claimed, missing, _blocked = claim_capabilities(["email.send"], inventory)
    assert [item.capability_id for item in claimed] == []
    assert [item.intent for item in missing] == ["email.send"]


def test_extract_weather_requires_web_fetch():
    intents = extract_required_intents("2分钟后帮我调用看一下今天天气")
    assert "web.fetch" in intents
    assert "email.send" not in intents


def test_extract_required_intents_email_and_weekly_report():
    intents = extract_required_intents("每周五给客户发周报邮件")
    assert "email.send" in intents
    assert "file.write" in intents
    assert "http.call" not in intents


def test_extract_drops_http_call_when_email_tokens_present():
    intents = extract_required_intents("发邮件", required_intents=["http.call"])
    assert "email.send" in intents
    assert "http.call" not in intents


def test_stdio_mcp_not_injectable_even_with_url():
    assert mcp_injectable("stdio", "https://example.com/mcp") is False
    assert mcp_injectable("sse", "https://example.com/mcp") is True
    assert mcp_injectable("sse", "") is False


def test_resolve_mcp_runtime_servers_is_pure():
    servers = [{
        "id": "gmail",
        "status": "installed",
        "enabled": True,
        "transport": "sse",
        "url": "https://example.com/mcp",
        "command": "",
        "headers": {},
        "tools": [
            {"id": "gmail-send-email", "name": "send_email"},
            {"id": "gmail-list-messages", "name": "list_messages"},
        ],
    }]
    specs = resolve_mcp_runtime_servers(
        ["gmail-send-email"],
        servers,
        allowed_server_ids={"gmail"},
    )
    assert len(specs) == 1
    assert specs[0]["enable_tools"] == ["send_email"]


def test_capability_gate_default_false_and_settings_merge(isolated_db):
    ensure_db()
    assert is_scheduled_jobs_enabled() is False
    assert is_capability_gate_enabled() is False
    set_scheduled_jobs_settings(enabled=True, capability_gate=False)
    set_scheduled_jobs_enabled(True)
    assert is_scheduled_jobs_enabled() is True
    assert is_capability_gate_enabled() is False


def test_catalog_and_routes_do_not_import_each_other_at_module_level():
    import ast

    import src.scheduler.capability_catalog as catalog

    tree = ast.parse(Path(catalog.__file__).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("src.server.routes"):
            raise AssertionError("capability_catalog 禁止模块级 import routes")
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("src.server.routes"):
                    raise AssertionError("capability_catalog 禁止模块级 import routes")
    for name in ("mcp_runtime.py", "http_runtime.py"):
        tree = ast.parse(Path(catalog.__file__).with_name(name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("src.server.routes"):
                raise AssertionError(f"{name} 禁止 import routes")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("src.server.routes"):
                        raise AssertionError(f"{name} 禁止 import routes")


def test_import_catalog_with_routes_does_not_explode():
    import src.scheduler.capability_catalog  # noqa: F401
    import src.server.routes.mcp  # noqa: F401
    import src.server.routes.tools  # noqa: F401
    import src.scheduler.service  # noqa: F401
