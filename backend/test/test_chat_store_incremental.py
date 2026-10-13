"""ChatStore 增量写：按行追加，坏 JSON 不丢行，附件和澄清字段能读回。"""

from __future__ import annotations

from src.storage import database


def _session(session_id: str = "s-1") -> dict:
    return {
        "id": session_id,
        "title": "会话",
        "agent_id": "default",
        "agent_name": "助手",
        "created_at": "2026-10-13T00:00:00+00:00",
        "updated_at": "2026-10-13T00:00:00+00:00",
    }


def test_append_keeps_files_and_clarification_fields():
    database.insert_chat_session_if_absent(_session())
    database.append_chat_message("s-1", {
        "id": "m-1",
        "role": "user",
        "content": "看附件",
        "files": [{"name": "a.txt", "path": "/tmp/a.txt"}],
        "clarification_requests": [{"id": "c1"}],
        "clarification_resolved": True,
        "created_at": "2026-10-13T00:00:01+00:00",
    })
    messages = database.list_chat_messages("s-1")
    assert messages[0]["files"] == [{"name": "a.txt", "path": "/tmp/a.txt"}]
    assert messages[0]["clarification_requests"] == [{"id": "c1"}]
    assert messages[0]["clarification_resolved"] is True
    assert database.get_chat_session("s-1")["message_count"] == 1


def test_bad_json_row_is_kept_and_later_writes_survive():
    database.insert_chat_session_if_absent(_session())
    database.append_chat_message("s-1", {
        "id": "m-good",
        "role": "user",
        "content": "完好",
        "created_at": "2026-10-13T00:00:01+00:00",
    })
    with database._lock, database._connect() as conn:
        conn.execute(
            "UPDATE chat_messages SET files = ? WHERE id = ?",
            ("{not-json", "m-good"),
        )
    loaded = database.list_chat_messages("s-1")
    assert loaded[0]["id"] == "m-good"
    assert loaded[0]["files"] == []
    database.append_chat_message("s-1", {
        "id": "m-next",
        "role": "assistant",
        "content": "还在",
        "created_at": "2026-10-13T00:00:02+00:00",
    })
    assert [item["id"] for item in database.list_chat_messages("s-1")] == ["m-good", "m-next"]


def test_insert_if_absent_returns_existing_row_and_delete_session_removes_messages():
    first = database.insert_chat_session_if_absent(_session())
    second = database.insert_chat_session_if_absent({**_session(), "title": "别的标题"})
    assert second["title"] == first["title"] == "会话"
    database.append_chat_message("s-1", {
        "id": "m-1",
        "role": "user",
        "content": "hi",
        "created_at": "2026-10-13T00:00:01+00:00",
    })
    database.delete_chat_session("s-1")
    assert database.get_chat_session("s-1") is None
    assert database.list_chat_messages("s-1") == []
