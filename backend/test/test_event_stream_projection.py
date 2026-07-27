"""Event Stream V3 离线 Block Core、事务顺序与恢复测试。"""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from src.server.routes.chat.projection.block_lifecycle import (
    BlockLifecycleCoordinator,
    BlockLifecycleError,
)
from src.server.routes.chat.projection.domain_events import (
    DomainEventDraft,
    DomainEventRepository,
)
from src.server.routes.chat.projection.frame_schema import DisplaySnapshot
from src.server.routes.chat.projection.projection_registry import (
    ProjectionMutation,
    default_projection_registry,
)
from src.server.routes.chat.projection.projection_runner import ProjectionRunner
from src.server.routes.chat.projection.projection_writer import (
    ProjectionConflict,
    ProjectionLeaseUnavailable,
    ProjectionStreamStore,
)
from src.server.routes.chat.projection.stream_service import ProjectionStreamService
from src.server.routes.chat.projection.source_normalizer import ChatProjectionNormalizer
from src.storage import database


@pytest.fixture()
def projection_db(tmp_path, monkeypatch):
    """每个测试使用独立 SQLite，避免污染本地开发数据。"""

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "event-stream.db")
    monkeypatch.setattr(database, "_initialized", False)
    database.ensure_db()
    yield
    monkeypatch.setattr(database, "_initialized", False)


def draft(
    event_type: str,
    aggregate_type: str,
    aggregate_id: str,
    revision: int,
    **data,
) -> DomainEventDraft:
    return DomainEventDraft(
        type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        revision=revision,
        data=data,
    )


def create_runtime(*, checkpoint_frame_interval: int = 128):
    events = DomainEventRepository()
    store = ProjectionStreamStore(
        checkpoint_frame_interval=checkpoint_frame_interval,
        checkpoint_seconds=3_600,
    )
    coordinator = BlockLifecycleCoordinator()
    runner = ProjectionRunner(
        events=events,
        store=store,
        registry=default_projection_registry(),
        coordinator=coordinator,
    )
    return events, store, coordinator, runner


def test_domain_events_use_one_global_ordinal_across_aggregates(projection_db) -> None:
    events = DomainEventRepository()

    first = events.append("stream-1", "run-1", draft("message.started", "message", "msg-1", 1))
    second = events.append("stream-1", "run-1", draft("tool.call.started", "tool", "tool-1", 1))
    third = events.append("stream-1", "run-1", draft("agent.output.started", "text", "text-1", 1))

    assert [first.stream_ordinal, second.stream_ordinal, third.stream_ordinal] == [1, 2, 3]
    assert [item.aggregate_type for item in events.list("stream-1")] == ["message", "tool", "text"]
    with pytest.raises(sqlite3.IntegrityError):
        events.append("stream-1", "run-1", draft("message.started", "message", "msg-1", 1))


def test_concurrent_aggregate_append_gets_unique_contiguous_ordinals(projection_db) -> None:
    events = DomainEventRepository()

    def append(index: int) -> int:
        event = events.append(
            "stream-concurrent",
            "run-concurrent",
            draft("audit.recorded", "audit", f"audit-{index}", 1, index=index),
        )
        return event.stream_ordinal

    with ThreadPoolExecutor(max_workers=8) as executor:
        ordinals = list(executor.map(append, range(24)))

    assert sorted(ordinals) == list(range(1, 25))
    assert [event.stream_ordinal for event in events.list("stream-concurrent")] == list(range(1, 25))


def test_append_in_transaction_rolls_back_aggregate_and_event_together(projection_db) -> None:
    events = DomainEventRepository()
    events.ensure_stream("stream-1", "run-1")
    with database._connect() as conn:
        conn.execute("CREATE TABLE test_aggregate(id TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("INSERT INTO test_aggregate VALUES ('aggregate-1', 'old')")
        conn.commit()

    with pytest.raises(RuntimeError, match="simulate crash"):
        with database._lock, database._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("UPDATE test_aggregate SET value = 'new' WHERE id = 'aggregate-1'")
            events.append_in_transaction(
                conn,
                "stream-1",
                "run-1",
                draft("message.started", "message", "msg-1", 1),
            )
            raise RuntimeError("simulate crash")

    with database._connect() as conn:
        value = conn.execute("SELECT value FROM test_aggregate").fetchone()["value"]
    assert value == "old"
    assert events.list("stream-1") == []


def test_block_lifecycle_rejects_invalid_order_and_cascades_failure() -> None:
    coordinator = BlockLifecycleCoordinator()
    empty = DisplaySnapshot()
    with pytest.raises(BlockLifecycleError, match="Block 不存在"):
        coordinator.apply_mutations(
            empty,
            [ProjectionMutation(op="delta", id="text-1", patch="late")],
        )

    opened, _ = coordinator.apply_mutations(
        empty,
        [
            ProjectionMutation(op="open", id="msg-1", kind="message", patch={}),
            ProjectionMutation(op="open", id="tool-1", parent="msg-1", kind="tool", patch={}),
            ProjectionMutation(op="open", id="text-1", parent="tool-1", kind="text", patch="work"),
        ],
    )
    with pytest.raises(BlockLifecycleError, match="open descendant"):
        coordinator.apply_mutations(
            opened,
            [ProjectionMutation(op="close", id="msg-1", outcome="completed")],
        )

    failed, frames = coordinator.apply_mutations(
        opened,
        [ProjectionMutation(op="close", id="msg-1", outcome="failed")],
    )
    assert [frame.id for frame in frames if hasattr(frame, "id")] == ["text-1", "tool-1", "msg-1"]
    assert failed.blocks_by_id["text-1"].outcome == "cancelled"
    assert failed.blocks_by_id["tool-1"].outcome == "cancelled"
    assert failed.blocks_by_id["msg-1"].outcome == "failed"


def test_runner_projects_message_text_tool_and_done(projection_db) -> None:
    events, store, coordinator, runner = create_runtime(checkpoint_frame_interval=4)
    stream_key, run_id = "stream-chat", "run-chat"
    fixtures = [
        draft("message.started", "message", "msg-1", 1, blockId="msg-1", role="assistant"),
        draft("agent.output.started", "text", "text-1", 1, blockId="text-1", parentId="msg-1"),
        draft("agent.output.delta", "text", "text-1", 2, blockId="text-1", delta="你好"),
        draft("agent.output.completed", "text", "text-1", 3, blockId="text-1"),
        draft(
            "tool.call.started",
            "tool",
            "tool-1",
            1,
            blockId="tool-1",
            parentId="msg-1",
            name="Read",
            inputPreview="a.txt",
            apiKey="must-not-project",
        ),
        draft("tool.result.delta", "tool", "tool-1", 2, blockId="tool-1", delta="摘要"),
        draft(
            "tool.result.completed",
            "tool",
            "tool-1",
            3,
            blockId="tool-1",
            status="completed",
            outputPreview="完成",
        ),
        draft("run.completed", "run", run_id, 1, messageId="msg-1"),
    ]
    for item in fixtures:
        events.append(stream_key, run_id, item)

    assert runner.run_available(stream_key, run_id) == len(fixtures)
    state = store.load_materialized(stream_key, coordinator)

    assert state.last_domain_ordinal == len(fixtures)
    assert state.snapshot.last_seq == 9
    assert state.snapshot.stream_done is True
    assert state.snapshot.order == ["msg-1", "text-1", "tool-1"]
    assert state.snapshot.blocks_by_id["text-1"].data == "你好"
    assert state.snapshot.blocks_by_id["tool-1"].data == {
        "name": "Read",
        "inputPreview": "a.txt",
        "outputPreview": "完成",
    }
    assert state.snapshot.blocks_by_id["msg-1"].outcome == "completed"
    assert state.snapshot_seq == state.last_seq


def test_snapshot_checkpoint_plus_tail_replay_equals_live_state(projection_db) -> None:
    events, store, coordinator, runner = create_runtime(checkpoint_frame_interval=3)
    stream_key, run_id = "stream-tail", "run-tail"
    fixtures = [
        draft("message.started", "message", "msg-1", 1, blockId="msg-1"),
        draft("agent.output.started", "text", "text-1", 1, blockId="text-1", parentId="msg-1"),
        draft("agent.output.delta", "text", "text-1", 2, blockId="text-1", delta="A"),
        draft("agent.output.delta", "text", "text-1", 3, blockId="text-1", delta="B"),
    ]
    for item in fixtures:
        events.append(stream_key, run_id, item)

    runner.run_available(stream_key, run_id)
    state = store.load_materialized(stream_key, coordinator)

    assert state.snapshot_seq == 3
    assert state.last_seq == 4
    assert state.snapshot.blocks_by_id["text-1"].data == "AB"
    with database._connect() as conn:
        persisted = conn.execute(
            "SELECT snapshot_seq, last_seq FROM projection_streams WHERE stream_key = ?",
            (stream_key,),
        ).fetchone()
    assert dict(persisted) == {"snapshot_seq": 3, "last_seq": 4}


def test_zero_frame_event_advances_checkpoint_once(projection_db) -> None:
    events, store, coordinator, runner = create_runtime()
    event = events.append(
        "stream-zero",
        "run-zero",
        draft("audit.only", "audit", "audit-1", 1, value="ignored"),
    )

    assert runner.run_available("stream-zero", "run-zero") == 1
    assert runner.run_available("stream-zero", "run-zero") == 0
    state = store.load_materialized("stream-zero", coordinator)
    assert state.last_domain_ordinal == event.stream_ordinal
    assert state.last_seq == 0
    assert store.frames_after("stream-zero", 0) == []


def test_projector_failure_does_not_advance_checkpoint(projection_db) -> None:
    events, store, coordinator, runner = create_runtime()
    events.append(
        "stream-broken",
        "run-broken",
        draft("agent.output.delta", "text", "text-1", 1, blockId="text-1", delta="orphan"),
    )

    with pytest.raises(BlockLifecycleError, match="Block 不存在"):
        runner.run_available("stream-broken", "run-broken")
    state = store.load_materialized("stream-broken", coordinator)
    assert state.last_domain_ordinal == 0
    assert state.last_seq == 0


def test_runner_fence_rejects_old_owner_after_takeover(projection_db) -> None:
    events, store, coordinator, _ = create_runtime()
    stream_key, run_id = "stream-fence", "run-fence"
    store.ensure_stream(stream_key, run_id)
    event = events.append(stream_key, run_id, draft("audit.only", "audit", "audit-1", 1))
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    first = store.acquire_lease(stream_key, "runner-1", now=start, lease_seconds=30)
    with pytest.raises(ProjectionLeaseUnavailable):
        store.acquire_lease(stream_key, "runner-2", now=start + timedelta(seconds=1))
    second = store.acquire_lease(stream_key, "runner-2", now=start + timedelta(seconds=31))

    with pytest.raises(ProjectionConflict, match="fence/lease"):
        store.commit_event(
            first,
            event,
            [],
            DisplaySnapshot(),
            now=start + timedelta(seconds=32),
        )
    committed = store.commit_event(
        second,
        event,
        [],
        DisplaySnapshot(),
        now=start + timedelta(seconds=32),
    )
    assert second.fence == first.fence + 1
    assert committed.last_domain_ordinal == 1


def test_retention_only_deletes_frames_covered_by_snapshot(projection_db) -> None:
    events, store, coordinator, runner = create_runtime(checkpoint_frame_interval=3)
    stream_key, run_id = "stream-retention", "run-retention"
    for item in [
        draft("message.started", "message", "msg-1", 1, blockId="msg-1"),
        draft("agent.output.started", "text", "text-1", 1, blockId="text-1", parentId="msg-1"),
        draft("agent.output.delta", "text", "text-1", 2, blockId="text-1", delta="A"),
        draft("agent.output.delta", "text", "text-1", 3, blockId="text-1", delta="B"),
    ]:
        events.append(stream_key, run_id, item)
    runner.run_available(stream_key, run_id)

    assert store.compact(stream_key) == 3
    assert [frame.seq for frame in store.frames_after(stream_key, 0)] == [4]
    restored = store.load_materialized(stream_key, coordinator)
    assert restored.snapshot.blocks_by_id["text-1"].data == "AB"


def test_stream_service_replays_contiguous_tail_or_falls_back_to_snapshot(projection_db) -> None:
    events, store, coordinator, runner = create_runtime(checkpoint_frame_interval=3)
    stream_key, run_id = "stream-service", "run-service"
    for item in [
        draft("message.started", "message", "msg-1", 1, blockId="msg-1"),
        draft("agent.output.started", "text", "text-1", 1, blockId="text-1", parentId="msg-1"),
        draft("agent.output.delta", "text", "text-1", 2, blockId="text-1", delta="A"),
        draft("agent.output.delta", "text", "text-1", 3, blockId="text-1", delta="B"),
    ]:
        events.append(stream_key, run_id, item)
    runner.run_available(stream_key, run_id)
    service = ProjectionStreamService(store=store, coordinator=coordinator)

    initial = service.read(stream_key)
    assert initial.snapshot_fallback is True
    assert initial.frames[0].seq == 4
    replay = service.read(stream_key, after_seq=2)
    assert replay.snapshot_fallback is False
    assert [frame.seq for frame in replay.frames] == [3, 4]

    store.compact(stream_key)
    expired = service.read(stream_key, after_seq=1)
    assert expired.snapshot_fallback is True
    assert expired.frames[0].seq == 4
    assert service.heartbeat_comment() == ": heartbeat\n\n"


def test_source_normalizer_closes_all_activity_before_run_completed(projection_db) -> None:
    events, store, coordinator, runner = create_runtime(checkpoint_frame_interval=8)
    normalizer = ChatProjectionNormalizer(
        stream_key="stream-normalized",
        run_id="run-normalized",
        session_id="session-normalized",
        events=events,
    )
    normalizer.start(agent_name="Agent")
    normalizer.ingest("REASONING_DELTA", {"delta": "分析"})
    normalizer.ingest("TOOL_CALL_START", {"tool_call_id": "call-1", "tool_call_name": "Read"})
    normalizer.ingest("TOOL_CALL_DELTA", {"tool_call_id": "call-1", "delta": "a.txt"})
    normalizer.ingest("TOOL_RESULT_TEXT_DELTA", {"tool_call_id": "call-1", "delta": "内容"})
    normalizer.ingest("TOOL_RESULT_END", {"tool_call_id": "call-1", "state": "completed"})
    normalizer.ingest("FINAL_ANSWER_DELTA", {"delta": "结果"})
    normalizer.ingest("HITL_REQUIRED", {
        "interactionId": "interaction-1",
        "kind": "hitl",
        "type": "REQUIRE_USER_CONFIRM",
        "sessionId": "session-normalized",
        "agentId": "agent-1",
        "agentName": "Agent",
        "runId": "run-normalized",
        "replyId": "reply-1",
        "scope": "subagent",
        "createdAt": "2026-01-01T00:00:00+00:00",
        "toolCalls": [],
    })
    normalizer.ingest("HITL_RESOLVED", {
        "payload": {"interactionId": "interaction-1", "action": "approve"},
    })
    normalizer.ingest("PLAN_REVIEW_REQUESTED", {
        "payload": {
            "planId": "plan-1",
            "status": "review_waiting",
            "revision": 1,
            "markdown": "- step",
            "steps": [{"id": "step-1", "title": "Step", "status": "pending"}],
        },
    })
    normalizer.ingest("SUBAGENT_STARTED", {
        "payload": {"invocationId": "sub-1", "subagentName": "explorer"},
    })
    normalizer.ingest("TEAM_DOMAIN_EVENT", {
        "teamRunId": "team-1",
        "snapshot": {"run": {"status": "running"}},
    })
    terminal_events = normalizer.ingest("RUN_COMPLETED", {
        "payload": {"completedAt": "2026-01-01T00:00:00+00:00"},
    })

    assert terminal_events[-1].type == "run.completed"
    assert runner.run_available("stream-normalized", "run-normalized") == len(events.list("stream-normalized"))
    state = store.load_materialized("stream-normalized", coordinator)
    assert state.snapshot.stream_done is True
    assert all(block.phase == "closed" for block in state.snapshot.blocks_by_id.values())
    assert state.snapshot.blocks_by_id["text:run-normalized"].data == "结果"
    assert state.snapshot.blocks_by_id["tool:call-1"].data["outputPreview"] == "内容"
    assert state.snapshot.blocks_by_id["approval:interaction-1"].outcome == "completed"
    assert state.snapshot.blocks_by_id["approval:interaction-1"].data["scope"] == "subagent"
    assert state.snapshot.blocks_by_id["approval:interaction-1"].data["replyId"] == "reply-1"
    assert state.snapshot.blocks_by_id["plan:plan-1"].data["status"] == "review_waiting"
    assert state.snapshot.blocks_by_id["plan:plan-1"].data["revision"] == 1
    assert state.snapshot.blocks_by_id["plan:plan-1"].data["steps"][0]["id"] == "step-1"
    assert state.snapshot.blocks_by_id["message:run-normalized"].outcome == "completed"


def test_subagent_block_stays_open_until_parent_consumes_result(projection_db) -> None:
    events, store, coordinator, runner = create_runtime(checkpoint_frame_interval=8)
    normalizer = ChatProjectionNormalizer(
        stream_key="stream-subagent-consume",
        run_id="run-subagent-consume",
        session_id="session-subagent-consume",
        events=events,
    )
    normalizer.start(agent_name="Agent")
    normalizer.ingest("SUBAGENT_STARTED", {"payload": {
        "invocationId": "sub-1",
        "subagentName": "explorer",
        "status": "queued",
        "deliveryStatus": "none",
    }})
    normalizer.ingest("SUBAGENT_COMPLETED", {"payload": {
        "invocationId": "sub-1",
        "subagentName": "explorer",
        "status": "completed",
        "deliveryStatus": "pending",
        "resultRef": ".agent-workstation/sessions/s/subagents/sub-1/result.md",
    }})
    runner.run_available("stream-subagent-consume", "run-subagent-consume")
    pending = store.load_materialized("stream-subagent-consume", coordinator)

    assert pending.snapshot.blocks_by_id["subagent:sub-1"].phase == "open"
    assert pending.snapshot.blocks_by_id["subagent:sub-1"].data["deliveryStatus"] == "pending"

    normalizer.ingest("SUBAGENT_CONSUMED", {"payload": {
        "invocationId": "sub-1",
        "subagentName": "explorer",
        "status": "completed",
        "taskStatus": "completed",
        "deliveryStatus": "consumed",
    }})
    runner.run_available("stream-subagent-consume", "run-subagent-consume")
    consumed = store.load_materialized("stream-subagent-consume", coordinator)

    assert consumed.snapshot.blocks_by_id["subagent:sub-1"].phase == "closed"
    assert consumed.snapshot.blocks_by_id["subagent:sub-1"].outcome == "completed"
    assert consumed.snapshot.blocks_by_id["subagent:sub-1"].data["deliveryStatus"] == "consumed"


def test_clarification_source_opens_approval_and_keeps_run_resumable(projection_db) -> None:
    events, store, coordinator, runner = create_runtime(checkpoint_frame_interval=8)
    normalizer = ChatProjectionNormalizer(
        stream_key="stream-clarification",
        run_id="run-clarification",
        session_id="session-clarification",
        events=events,
    )
    normalizer.start(agent_name="Agent")
    produced = normalizer.ingest("CLARIFICATION_REQUIRED", {
        "id": "clarification-1",
        "title": "需要补充信息",
        "questions": [{"id": "target", "type": "text", "label": "目标"}],
    })

    assert produced[-1].type == "interaction.started"
    runner.run_available("stream-clarification", "run-clarification")
    state = store.load_materialized("stream-clarification", coordinator)
    approval = state.snapshot.blocks_by_id["approval:clarification-1"]
    assert approval.phase == "open"
    assert approval.data["status"] == "waiting"
    assert state.snapshot.stream_done is False
