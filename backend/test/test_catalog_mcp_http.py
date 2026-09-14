"""MCP / HTTP 定义文件化与密钥剥离。"""

from pathlib import Path

from src.catalog.http_files import HttpToolFileRecord, parse_http_yaml, write_http_file
from src.catalog.mcp_files import McpFileRecord, parse_mcp_yaml, write_mcp_file
from src.catalog.secrets import resolve_headers
from src.config.secret_store import get_named_secret


def test_mcp_yaml_strips_authorization(tmp_path: Path) -> None:
    path = tmp_path / "custom-mcp.yaml"
    record = McpFileRecord(
        id="custom-mcp",
        name="Custom",
        url="https://example.com/mcp",
        headers={"Authorization": "Bearer super-secret", "X-Debug": "1"},
    )
    write_mcp_file(path, record)
    text = path.read_text(encoding="utf-8")
    assert "super-secret" not in text
    assert "Bearer" not in text
    parsed = parse_mcp_yaml(text, path=path, workspace_id="default", source_scope="workspace")
    resolved = resolve_headers(parsed.headers, parsed.header_refs)
    assert resolved["Authorization"] == "Bearer super-secret"
    assert resolved["X-Debug"] == "1"
    assert get_named_secret(parsed.header_refs["Authorization"]) == "Bearer super-secret"


def test_http_yaml_stores_auth_config_in_secret(tmp_path: Path) -> None:
    path = tmp_path / "tool-1.yaml"
    record = HttpToolFileRecord(
        id="tool-1",
        name="Search",
        url="https://example.com/search",
        auth_type="bearer",
        headers={"Accept": "application/json"},
    )
    write_http_file(path, record, auth_config={"token": "tok-123"})
    text = path.read_text(encoding="utf-8")
    assert "tok-123" not in text
    assert "auth_config" not in text
    parsed = parse_http_yaml(text, path=path, workspace_id="default", source_scope="workspace")
    from src.catalog.http_files import runtime_auth_config

    assert runtime_auth_config(parsed)["token"] == "tok-123"
    assert parsed.headers.get("Accept") == "application/json"


def test_plaintext_secret_yaml_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text(
        "id: bad\nname: Bad\nurl: https://x\nheaders:\n  Authorization: Bearer leaked\n",
        encoding="utf-8",
    )
    try:
        parse_mcp_yaml(path.read_text(encoding="utf-8"), path=path, workspace_id="default", source_scope="workspace")
        raise AssertionError("should reject")
    except ValueError as exc:
        assert "明文密钥" in str(exc)


def test_mcp_http_migration_exports_sqlite(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("src.config.runtime_paths.USER_DATA_ROOT", tmp_path)
    from src.catalog.migrate_mcp_http import (
        HTTP_MIGRATION_KEY,
        MCP_MIGRATION_KEY,
        ensure_http_file_catalog,
        ensure_mcp_file_catalog,
        is_http_catalog_migrated,
        is_mcp_catalog_migrated,
    )
    from src.catalog.paths import http_tools_dir, mcp_dir
    from src.storage import database
    from src.storage.database import upsert_json_record

    upsert_json_record("catalog", MCP_MIGRATION_KEY, {"status": ""})
    upsert_json_record("catalog", HTTP_MIGRATION_KEY, {"status": ""})
    database.replace_mcp_servers({
        "custom-remote": {
            "id": "custom-remote",
            "name": "Remote",
            "description": "",
            "category": "自定义",
            "transport": "sse",
            "command": "",
            "url": "https://example.com/mcp",
            "homepage": "",
            "headers": {"Authorization": "Bearer migrate-me"},
            "tags": [],
            "tools": [],
            "status": "installed",
            "enabled": True,
            "updatedAt": "2026-09-10T00:00:00+00:00",
            "workspaceId": "default",
        }
    })
    database.upsert_http_tool({
        "id": "http-mig",
        "name": "Mig",
        "url": "https://example.com/api",
        "authType": "bearer",
        "authConfig": {"token": "http-secret"},
        "headers": {},
        "workspaceId": "default",
        "createdAt": "2026-09-10T00:00:00+00:00",
        "updatedAt": "2026-09-10T00:00:00+00:00",
    })
    ensure_mcp_file_catalog(builtin_ids=set())
    ensure_http_file_catalog()
    assert is_mcp_catalog_migrated()
    assert is_http_catalog_migrated()
    mcp_text = (mcp_dir("default") / "custom-remote.yaml").read_text(encoding="utf-8")
    http_text = (http_tools_dir("default") / "http-mig.yaml").read_text(encoding="utf-8")
    assert "migrate-me" not in mcp_text
    assert "http-secret" not in http_text
    assert all(row["id"] != "http-mig" for row in database.list_http_tools())
