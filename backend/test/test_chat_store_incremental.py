"""ChatStore 增量写：按行追加，坏 JSON 不丢行，附件和澄清字段能读回。"""

from __future__ import annotations

from threading import Thread

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


def test_tool_runs_round_trip_and_old_string_blobs_still_load():
    from src.server.routes.chat.models import ChatModelCacheRun, ChatToolRun
    from src.server.routes.chat.store import message_model

    database.insert_chat_session_if_absent(_session("s-tools"))
    tool = ChatToolRun(id="call-1", name="timeline_retrieve", args='{"q":"乙"}', result="{}", status="completed")
    cache = ChatModelCacheRun(
        id="cache-1",
        model_name="grok-4.7",
        input_tokens=10,
        output_tokens=2,
        created_at="2026-10-13T14:19:03.789140",
    )
    database.append_chat_message("s-tools", {
        "id": "m-tools",
        "role": "assistant",
        "content": "查过了",
        "tool_calls": [tool],
        "model_caches": [cache],
        "created_at": "2026-10-13T00:00:01+00:00",
    })
    loaded = message_model(database.list_chat_messages("s-tools")[0])
    assert loaded.tool_calls[0].name == "timeline_retrieve"
    assert loaded.model_caches[0].model_name == "grok-4.7"

    legacy = (
        '["id=\'call-1\' name=\'timeline_retrieve\' args=\'{\\"q\\": \\"乙\\"}\' '
        'result=\'{}\' status=\'completed\'"]'
    )
    with database._lock, database._connect() as conn:
        conn.execute("UPDATE chat_messages SET tool_calls = ? WHERE id = ?", (legacy, "m-tools"))
    again = message_model(database.list_chat_messages("s-tools")[0])
    assert again.tool_calls[0].id == "call-1"
    assert again.tool_calls[0].status == "completed"


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


def test_concurrent_appends_keep_unique_order():
    database.insert_chat_session_if_absent(_session("s-concurrent"))

    def worker(worker_id: int) -> None:
        for index in range(50):
            database.append_chat_message("s-concurrent", {
                "id": f"m-{worker_id}-{index}",
                "role": "user",
                "content": f"{worker_id}:{index}",
                "created_at": "2026-10-13T00:00:01+00:00",
            })

    threads = [Thread(target=worker, args=(worker_id,)) for worker_id in (1, 2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    messages = database.list_chat_messages("s-concurrent")
    assert len(messages) == 100
    assert len({item["sort_order"] for item in messages}) == 100
    assert len({item["id"] for item in messages}) == 100


def test_delete_session_cascades_run_events():
    database.insert_chat_session_if_absent(_session())
    database.append_run_event(session_id="s-1", event_type="NOTE", title="留下")
    database.delete_chat_session("s-1")
    assert database.list_run_events("s-1") == []


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
