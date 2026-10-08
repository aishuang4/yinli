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
from src.server.routes.chat.team_runtime.store import TeamEventPublisher, TeamStore
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
        "id": "tevt-1",
        "seq": 1,
        "teamRunId": "team-1",
        "chatRunId": "run-normalized",
        "type": "run.created",
        "actorId": "leader",
        "status": "running",
        "timestamp": "2026-01-01T00:00:00+00:00",
        "counts": {"members": 1, "tasks": 0},
        "payload": {
            "run": {"status": "running", "phase": "created"},
            "leader": {"id": "leader", "name": "Leader", "role": "leader", "status": "created"},
        },
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
    assert state.snapshot.blocks_by_id["team:team-1"].outcome == "completed"
    assert state.snapshot.blocks_by_id["team-member:team-1:leader"].outcome == "completed"
    assert state.snapshot.blocks_by_id["message:run-normalized"].outcome == "completed"
    assert "answerGeneration" not in state.snapshot.blocks_by_id["message:run-normalized"].data


def test_generation_scoped_text_keeps_only_the_ready_block_as_answer(projection_db) -> None:
    events, store, coordinator, runner = create_runtime()
    normalizer = ChatProjectionNormalizer(
        stream_key="stream-answer",
        run_id="run-answer",
        session_id="session-answer",
        events=events,
    )
    normalizer.start(agent_name="Agent")
    normalizer.ingest("FINAL_ANSWER_DELTA", {"delta": "先看文件。", "generation": 1})
    normalizer.ingest("TOOL_CALL_START", {"tool_call_id": "call-1", "tool_call_name": "Read"})
    normalizer.ingest("FINAL_ANSWER_DELTA", {"delta": "文件里写着结果。", "generation": 2})
    normalizer.ingest("ANSWER_READY", {"generation": 2})
    normalizer.ingest("RUN_COMPLETED", {"payload": {}})

    assert runner.run_available("stream-answer", "run-answer") == len(events.list("stream-answer"))
    state = store.load_materialized("stream-answer", coordinator)
    assert state.snapshot.blocks_by_id["text:run-answer:1"].data == "先看文件。"
    assert state.snapshot.blocks_by_id["text:run-answer:2"].data == "文件里写着结果。"
    assert state.snapshot.blocks_by_id["message:run-answer"].data["answerGeneration"] == "text:run-answer:2"


def test_source_normalizer_projects_context_usage_and_closes_before_done(projection_db) -> None:
    events, store, coordinator, runner = create_runtime()
    normalizer = ChatProjectionNormalizer(
        stream_key="stream-context",
        run_id="run-context",
        session_id="session-context",
        events=events,
    )
    normalizer.start(agent_name="Agent")
    started = normalizer.ingest("CONTEXT_USAGE", {
        "contextWindow": 32_768,
        "triggerRatio": 0.8,
        "triggerTokens": 26_214,
        "estimatedTokens": 1_200,
        "compressed": 0,
        "overTrigger": 0,
        "contextMessages": 4,
    })
    updated = normalizer.ingest("CONTEXT_USAGE", {
        "inputTokens": 1_800,
        "outputTokens": 40,
        "overTrigger": 0,
    })
    normalizer.ingest("FINAL_ANSWER_DELTA", {"delta": "结果"})
    terminal = normalizer.ingest("RUN_COMPLETED", {"payload": {}})

    assert started[0].type == "context.started"
    assert updated[0].type == "context.updated"
    assert terminal[-1].type == "run.completed"
    assert runner.run_available("stream-context", "run-context") == len(events.list("stream-context"))
    state = store.load_materialized("stream-context", coordinator)
    block = state.snapshot.blocks_by_id["context:run-context"]
    assert state.snapshot.stream_done is True
    assert block.phase == "closed"
    assert block.data["estimatedTokens"] == 1_200
    assert block.data["inputTokens"] == 1_800
    assert block.data["contextWindow"] == 32_768
    assert all(item.phase == "closed" for item in state.snapshot.blocks_by_id.values())


def test_real_team_outbox_projects_incremental_block_tree(projection_db, tmp_path) -> None:
    """真实 TeamStore 事件必须生成规范化子树，且发布事件不得携带完整 snapshot。"""

    team_store = TeamStore.create(
        session_id="session-team",
        chat_run_id="run-team",
        goal="并行审计",
        leader_agent_id="agent-leader",
        leader_name="Leader",
        project_id="project-team",
        root=tmp_path / "team-runtime",
    )
    member = team_store.spawn_member(name="Reviewer", role="review", runtime_prompt="review")
    task = team_store.create_task(
        title="检查投影",
        brief="读取事件投影代码",
        created_by="leader",
    )
    team_store.claim_task(task["id"], member["id"])
    team_store.record_member_event(
        "member.thinking.delta",
        member_id=member["id"],
        task_id=task["id"],
        payload={"delta": "先检查 normalizer。"},
    )
    team_store.record_member_event(
        "member.tool.started",
        member_id=member["id"],
        task_id=task["id"],
        payload={"toolCallId": "call-read", "name": "Read"},
    )
    team_store.record_member_event(
        "member.tool.completed",
        member_id=member["id"],
        task_id=task["id"],
        status="completed",
        payload={"toolCallId": "call-read", "name": "Read", "state": "completed"},
    )
    team_store.record_member_event(
        "member.output.delta",
        member_id=member["id"],
        task_id=task["id"],
        payload={"delta": "投影链路职责清晰。"},
    )
    team_store.record_member_event(
        "member.tool.started",
        member_id=member["id"],
        task_id=task["id"],
        payload={"toolCallId": "call-denied", "name": "Bash"},
    )
    team_store.record_member_event(
        "member.tool.denied",
        member_id=member["id"],
        task_id=task["id"],
        status="denied",
        payload={
            "toolCallId": "call-denied",
            "name": "Bash",
            "reason": "requires_user_confirmation",
        },
    )
    team_store.record_member_event(
        "member.tool.failed",
        member_id=member["id"],
        task_id=task["id"],
        status="failed",
        payload={"toolCallId": "call-denied", "name": "Bash", "state": "denied"},
    )
    team_store.record_member_event(
        "member.tool.started",
        member_id=member["id"],
        task_id=task["id"],
        payload={"toolCallId": "call-submit", "name": "team_submit_result"},
    )
    team_store.complete_task(task["id"], member["id"], "投影链路职责清晰。")
    team_store.record_member_event(
        "member.tool.completed",
        member_id=member["id"],
        task_id=task["id"],
        status="completed",
        payload={
            "toolCallId": "call-submit",
            "name": "team_submit_result",
            "state": "completed",
        },
    )
    team_store.record_member_event(
        "member.output.delta",
        member_id=member["id"],
        task_id=task["id"],
        payload={"delta": "任务提交后的迟到总结不应重新打开已关闭活动。"},
    )
    team_store.set_member_status(member["id"], "idle")
    team_store.read_inbox("leader")
    team_store.set_phase("synthesizing")
    team_store.complete_run()

    publisher = TeamEventPublisher(team_store)
    source_events = publisher.drain()
    assert source_events
    assert all("snapshot" not in event for event in source_events)
    assert publisher.drain() == []

    events, display_store, coordinator, runner = create_runtime(checkpoint_frame_interval=5)
    normalizer = ChatProjectionNormalizer(
        stream_key="stream-team",
        run_id="run-team",
        session_id="session-team",
        events=events,
    )
    normalizer.start(agent_name="Leader")
    projected_count = 0
    for source in source_events:
        normalizer.ingest("TEAM_DOMAIN_EVENT", source)
        if source["type"] == "task.completed":
            projected_count += runner.run_available("stream-team", "run-team")
            intermediate = display_store.load_materialized("stream-team", coordinator).snapshot
            pending_task = intermediate.blocks_by_id[
                f"team-task:{team_store.ref.id}:{task['id']}"
            ]
            pending_tool = intermediate.blocks_by_id[
                f"tool:team:{team_store.ref.id}:call-submit"
            ]
            assert pending_task.phase == "open"
            assert pending_task.data["status"] == "succeeded"
            assert pending_tool.phase == "open"
    normalizer.ingest("FINAL_ANSWER_REPLACE", {"content": "Team 审计完成。"})
    normalizer.ingest("RUN_COMPLETED", {"payload": {}})

    projected_count += runner.run_available("stream-team", "run-team")
    assert projected_count == len(events.list("stream-team"))
    state = display_store.load_materialized("stream-team", coordinator)
    snapshot = state.snapshot
    team_id = team_store.ref.id
    team_block = snapshot.blocks_by_id[f"team:{team_id}"]
    member_block = snapshot.blocks_by_id[f"team-member:{team_id}:{member['id']}"]
    task_block = snapshot.blocks_by_id[f"team-task:{team_id}:{task['id']}"]
    thinking_block = snapshot.blocks_by_id[f"team-activity:{team_id}:{task['id']}:thinking"]
    output_block = snapshot.blocks_by_id[f"team-activity:{team_id}:{task['id']}:output"]
    tool_block = snapshot.blocks_by_id[f"tool:team:{team_id}:call-read"]
    submit_tool_block = snapshot.blocks_by_id[f"tool:team:{team_id}:call-submit"]
    denied_tool_block = snapshot.blocks_by_id[f"tool:team:{team_id}:call-denied"]
    mail_blocks = [block for block in snapshot.blocks_by_id.values() if block.kind == "team-mail"]

    assert snapshot.stream_done is True
    assert team_block.data["projectId"] == "project-team"
    assert team_block.data["counts"]["succeeded"] == 1
    assert team_block.outcome == "completed"
    assert member_block.data["name"] == "Reviewer"
    assert member_block.outcome == "completed"
    assert task_block.data["briefPreview"] == "读取事件投影代码"
    assert task_block.data["resultPreview"] == "投影链路职责清晰。"
    assert task_block.outcome == "completed"
    assert thinking_block.data["content"] == "先检查 normalizer。"
    assert output_block.data["content"] == "投影链路职责清晰。"
    assert tool_block.data["name"] == "Read"
    assert tool_block.outcome == "completed"
    assert submit_tool_block.outcome == "completed"
    assert denied_tool_block.outcome == "failed"
    assert mail_blocks and mail_blocks[0].data["deliveryStatus"] == "read"
    assert snapshot.blocks_by_id["message:run-team"].data["answerGeneration"] == "text:run-team"
    assert all(block.phase == "closed" for block in snapshot.blocks_by_id.values())


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


def test_team_task_retry_reopens_projection_and_keeps_retry_output(projection_db, tmp_path) -> None:
    """可重试失败不得永久关闭 Task Block，否则重派后的过程对前端不可见，看起来像整队卡住。"""

    team_store = TeamStore.create(
        session_id="session-team-retry",
        chat_run_id="run-team-retry",
        goal="重派可见",
        leader_agent_id="agent-leader",
        leader_name="Leader",
        project_id="project-team",
        root=tmp_path / "team-runtime-retry",
    )
    member = team_store.spawn_member(name="Writer", role="write", runtime_prompt="write")
    task = team_store.create_task(title="写摘要", brief="写一段摘要", created_by="leader", assigned_member_id=member["id"])
    team_store.claim_task(task["id"], member["id"])
    team_store.fail_task(task["id"], member["id"], "Tool base directory is outside filesystem scope: /tmp/session")
    team_store.retry_failed_task(task["id"])
    team_store.set_member_status(member["id"], "idle")
    team_store.claim_task(task["id"], member["id"])
    team_store.record_member_event(
        "member.output.delta",
        member_id=member["id"],
        task_id=task["id"],
        payload={"delta": "重派后的可见输出"},
    )

    events, display_store, coordinator, runner = create_runtime()
    normalizer = ChatProjectionNormalizer(
        stream_key="stream-team-retry",
        run_id="run-team-retry",
        session_id="session-team-retry",
        events=events,
    )
    normalizer.start(agent_name="Leader")
    for source in TeamEventPublisher(team_store).drain():
        normalizer.ingest("TEAM_DOMAIN_EVENT", source)
    runner.run_available("stream-team-retry", "run-team-retry")
    snapshot = display_store.load_materialized("stream-team-retry", coordinator).snapshot
    task_block = snapshot.blocks_by_id[f"team-task:{team_store.ref.id}:{task['id']}"]
    output_block = snapshot.blocks_by_id[f"team-activity:{team_store.ref.id}:{task['id']}:output"]

    assert task_block.phase == "open"
    assert task_block.data["status"] == "running"
    assert output_block.data["content"] == "重派后的可见输出"
