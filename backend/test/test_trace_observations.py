"""Trace Observation 存储与安全写入。"""

from __future__ import annotations

import pytest

from src.server.routes.chat.observation_writer import (
    close_observation,
    open_observation,
    patch_observation,
)
from src.storage import database
from src.storage.database import (
    get_trace_observation,
    insert_trace_observation,
    list_trace_observations,
)


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "trace-observations.db")
    monkeypatch.setattr(database, "_initialized", False)
    yield
    monkeypatch.setattr(database, "_initialized", False)


def test_observation_round_trip_and_list_by_run(isolated_db) -> None:
    first = insert_trace_observation({
        "id": "obs-root",
        "runId": "run-1",
        "sessionId": "sess-1",
        "kind": "run",
        "name": "本轮",
        "input": {"message": "写周报"},
    })
    insert_trace_observation({
        "id": "obs-tool",
        "runId": "run-1",
        "sessionId": "sess-1",
        "kind": "tool",
        "name": "Bash",
        "parentId": "obs-root",
        "input": {"command": "ls"},
    })
    insert_trace_observation({
        "id": "obs-other",
        "runId": "run-2",
        "sessionId": "sess-1",
        "kind": "run",
        "name": "另一轮",
    })

    listed = list_trace_observations("run-1")
    assert [item["id"] for item in listed] == ["obs-root", "obs-tool"]
    assert first["kind"] == "run"
    assert get_trace_observation("obs-tool")["parentId"] == "obs-root"


def test_open_close_computes_duration_and_skips_sandbox(isolated_db) -> None:
    opened = open_observation(
        persist=True,
        run_id="run-a",
        session_id="sess-a",
        kind="tool",
        name="Read",
        input_data={"path": "a.md"},
    )
    assert opened
    close_observation(
        persist=True,
        observation_id=opened,
        status="completed",
        output={"text": "ok"},
    )
    row = get_trace_observation(opened)
    assert row["status"] == "completed"
    assert row["output"]["text"] == "ok"
    assert row["endedAt"]
    assert row["durationMs"] is not None
    assert row["durationMs"] >= 0

    skipped = open_observation(
        persist=False,
        run_id="run-a",
        session_id="sess-a",
        kind="tool",
        name="Write",
    )
    assert skipped is None
    assert len(list_trace_observations("run-a")) == 1


def test_open_truncates_large_input(isolated_db) -> None:
    opened = open_observation(
        persist=True,
        run_id="run-b",
        session_id="sess-b",
        kind="generation",
        name="model",
        input_data={"prompt": "字" * 20_000},
    )
    row = get_trace_observation(opened)
    encoded = str(row["input"].get("prompt") or row["input"].get("preview") or "")
    assert len(encoded) < 20_000


def test_writer_swallows_storage_errors(monkeypatch) -> None:
    def boom(*_args, **_kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(
        "src.storage.database.insert_trace_observation",
        boom,
    )
    assert open_observation(
        persist=True,
        run_id="run-x",
        session_id="sess-x",
        kind="run",
        name="root",
    ) is None
    close_observation(persist=True, observation_id="missing", status="failed")
    patch_observation(persist=True, observation_id="missing", output={"a": 1})
