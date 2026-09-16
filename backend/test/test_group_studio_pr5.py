"""群聊工作室 PR 5：独立 SSE 与 Host 串行讨论。"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from src.group_studio.host_runtime import build_host_runtime_spec, run_host_discussion
from src.group_studio.models import GROUP_STUDIO_HOST_AGENT_ID
from src.group_studio.settings import set_group_studio_settings
from src.group_studio.tools_host import HOST_STOP_TOOLS
from src.server.routes import group_studio, settings as settings_routes
from src.storage import database
from src.storage.database import (
    DEFAULT_PROJECT_ID,
    ensure_db,
    get_group_studio_snapshot,
    list_group_studio_events_after,
)


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "agent_workstation.db")
    monkeypatch.setattr(database, "_initialized", False)
    yield
    database._initialized = False


@pytest.fixture()
def client(isolated_db):
    ensure_db()
    set_group_studio_settings(enabled=True)
    app = FastAPI()
    app.include_router(group_studio.router, prefix="/api/group-studio")
    app.include_router(settings_routes.router, prefix="/api/settings")
    with TestClient(app) as test_client:
        yield test_client


def _create_discussing(client: TestClient, session_id: str = "sess-pr5") -> dict:
    created = client.post("/api/group-studio/from-chat", json={
        "projectId": DEFAULT_PROJECT_ID,
        "chatSessionId": session_id,
        "agentId": "default",
    })
    assert created.status_code == 200
    group_id = created.json()["id"]
    from test.group_studio_helpers import confirm_intake_for_tests

    confirm_intake_for_tests(client, group_id, goal="写一份竞品分析")
    return client.get(f"/api/group-studio/{group_id}").json()


class _FakeEvent:
    def __init__(self, **payload: object) -> None:
        self._payload = payload

    def model_dump(self, mode: str = "json") -> dict:
        return dict(self._payload)


class _FakeAgent:
    async def reply_stream(self, _message: object):
        yield _FakeEvent(type="TEXT_BLOCK_DELTA", delta="先对齐目标，")
        yield _FakeEvent(type="TEXT_BLOCK_DELTA", delta="然后请研究员发言。")
        yield _FakeEvent(type="REPLY_END", finished_reason="completed")


class _TalkAgent:
    """本轮不调 assign，只发言。"""

    async def reply_stream(self, _message: object):
        yield _FakeEvent(type="TEXT_BLOCK_DELTA", delta="先对齐目标。")
        yield _FakeEvent(type="REPLY_END", finished_reason="completed")


def test_sse_state_envelope_format(client):
    from src.group_studio.stream import format_sse

    studio = client.post("/api/group-studio/from-chat", json={
        "projectId": DEFAULT_PROJECT_ID,
        "chatSessionId": "sess-sse",
        "agentId": "default",
    }).json()
    text = format_sse({
        "seq": None,
        "groupId": studio["id"],
        "runId": None,
        "type": "GROUP_STUDIO_STATE",
        "status": studio["status"],
        "payload": studio,
        "createdAt": studio["createdAt"],
    })
    assert text.startswith("event: GROUP_STUDIO_STATE\n")
    assert studio["id"] in text
    routes = {getattr(route, "path", "") for route in client.app.routes}
    assert "/api/group-studio/{group_id}/events" in routes


def test_host_turn_persists_message_but_not_delta(client):
    import asyncio

    studio = _create_discussing(client)
    group_id = studio["id"]
    asyncio.run(run_host_discussion(group_id, agent_factory=lambda _spec: _FakeAgent()))
    snapshot = get_group_studio_snapshot(group_id)
    assert snapshot is not None
    messages = snapshot["messages"]
    assert messages
    assert "对齐目标" in messages[-1]["body"]
    assert messages[-1]["role"] == "host"
    host = next(item for item in snapshot["members"] if item["role"] == "host")
    assert host["lastSpokeAt"]
    persisted = list_group_studio_events_after(group_id, 0)
    types = [item["type"] for item in persisted]
    assert "GROUP_MESSAGE_DELTA" not in types
    assert "GROUP_MESSAGE_OPEN" in types
    assert "GROUP_MESSAGE_CLOSE" in types
    assert "GROUP_FLOOR" in types
    assert "GROUP_RUN_ENDED" in types


def test_host_spec_includes_invite_tools(client):
    studio = _create_discussing(client)
    host = next(item for item in studio["members"] if item["role"] == "host")
    spec, _ctx, tools = build_host_runtime_spec(studio, host)
    names = {tool.name for tool in tools}
    assert "group_list_available_agents" in names
    assert "group_invite_member" in names
    assert "group_invite_member 拉人" in spec.system_prompt


def test_host_drafting_prompt_allows_preinvite(client):
    created = client.post("/api/group-studio/from-chat", json={
        "projectId": DEFAULT_PROJECT_ID,
        "chatSessionId": "sess-pr5-draft-invite",
        "agentId": "default",
    })
    studio = created.json()
    host = next(item for item in studio["members"] if item["role"] == "host")
    spec, _ctx, tools = build_host_runtime_spec(studio, host)
    names = {tool.name for tool in tools}
    assert "group_invite_member" in names
    assert "group_invite_member 预邀" in spec.system_prompt
    assert "不要 group_assign_floor" in spec.system_prompt
    assert "不要 group_invite_member" not in spec.system_prompt


class _ThinkingAgent:
    async def reply_stream(self, _message: object):
        yield _FakeEvent(type="THINKING_BLOCK_DELTA", delta="内心推理不该进正文。")
        yield _FakeEvent(type="TEXT_BLOCK_DELTA", delta="请行情研究员先说。")
        yield _FakeEvent(type="REPLY_END", finished_reason="completed")


def test_host_thinking_stays_out_of_body(client):
    import asyncio

    studio = _create_discussing(client)
    asyncio.run(run_host_discussion(studio["id"], agent_factory=lambda _spec: _ThinkingAgent()))
    snapshot = get_group_studio_snapshot(studio["id"])
    assert snapshot is not None
    message = snapshot["messages"][-1]
    assert "请行情研究员先说" in message["body"]
    assert "内心推理" not in message["body"]
    assert "内心推理" in message["thinking"]


def test_host_blocks_without_api_key(client, monkeypatch):
    import asyncio
    from src.group_studio import host_runtime
    from src.group_studio.errors import MODEL_NOT_CONFIGURED

    monkeypatch.setattr(
        host_runtime,
        "get_group_studio_model_error",
        lambda *_args: "DeepSeek API Key 未配置。请在系统设置中配置模型供应商和 API Key。",
    )
    called = {"create": False}

    def boom(_spec):
        called["create"] = True
        raise AssertionError("must not construct host agent without api key")

    monkeypatch.setattr(host_runtime, "create_main_agent_from_spec", boom)
    studio = _create_discussing(client, "sess-no-key")
    asyncio.run(run_host_discussion(studio["id"]))
    events = list_group_studio_events_after(studio["id"], 0)
    error = next(item for item in events if item["type"] == "GROUP_ERROR")
    assert error["payload"]["code"] == MODEL_NOT_CONFIGURED
    assert "API Key" in error["payload"]["message"]
    assert called["create"] is False
    snapshot = get_group_studio_snapshot(studio["id"])
    assert snapshot is not None
    assert snapshot["currentRunId"] is None
    assert snapshot["messages"] == []


def test_host_spec_has_no_default_tools(client):
    studio = _create_discussing(client, "sess-spec")
    host = next(item for item in studio["members"] if item["role"] == "host")
    spec, _ctx, tools = build_host_runtime_spec(studio, host)
    assert spec.include_default_tools is False
    assert spec.parent_agent_id == GROUP_STUDIO_HOST_AGENT_ID
    assert spec.tool_name_denylist >= {"AskUser", "FinalAnswerStart"}
    names = {tool.name for tool in tools}
    assert "group_assign_floor" in names
    assert HOST_STOP_TOOLS <= names
    assert spec.model_options.max_react_iters == 16
    # Host 没有 Skill，只挂一级整理 + 压缩可观测
    assert [type(item).__name__ for item in spec.middlewares] == [
        "ContextHygieneMiddleware",
        "ContextCompressionMiddleware",
    ]


def test_pytest_does_not_auto_start_host(client):
    studio = _create_discussing(client, "sess-noauto")
    snapshot = get_group_studio_snapshot(studio["id"])
    assert snapshot is not None
    assert snapshot["currentRunId"] is None
    assert snapshot["messages"] == []


class _AssigningAgent:
    """调用首次绑定的 group_assign_floor，模拟缓存 Agent 仍持有旧工具。"""

    def __init__(self, tools: list, member_id: str) -> None:
        self._tools = tools
        self._member_id = member_id

    async def reply_stream(self, _message: object):
        tool = next(item for item in self._tools if item.name == "group_assign_floor")
        await tool(memberId=self._member_id)
        yield _FakeEvent(type="TEXT_BLOCK_DELTA", delta="请下一位发言。")
        yield _FakeEvent(type="TOOL_RESULT_END", tool_call_name="group_assign_floor", state="success")
        yield _FakeEvent(type="REPLY_END", finished_reason="completed")


class _ReuseFactory:
    """第二次返回同一 Agent，工具仍是第一轮 extra_tools。"""

    def __init__(self, member_id: str) -> None:
        self.member_id = member_id
        self.agent: _AssigningAgent | None = None
        self.calls = 0

    def __call__(self, spec: object) -> _AssigningAgent:
        self.calls += 1
        extra = list(getattr(spec, "extra_tools", []) or [])
        if self.agent is None:
            self.agent = _AssigningAgent(extra, self.member_id)
        return self.agent


def test_cached_host_assign_uses_current_run_and_starts_pending(client):
    import asyncio

    from src.storage.database import get_group_studio_pending_speaker

    studio = _create_discussing(client, "sess-cache-assign")
    worker = next(item for item in studio["members"] if item["role"] == "worker")
    factory = _ReuseFactory(worker["id"])
    first = asyncio.run(run_host_discussion(studio["id"], agent_factory=factory))
    assert first == worker["id"]
    first_events = list_group_studio_events_after(studio["id"], 0)
    first_assign = [item for item in first_events if item["type"] == "GROUP_FLOOR" and item["payload"].get("reason") == "host_assign"][-1]
    first_run = first_assign["payload"]["runId"]

    second = asyncio.run(run_host_discussion(studio["id"], agent_factory=factory))
    assert factory.calls == 2
    assert second == worker["id"]
    events = list_group_studio_events_after(studio["id"], 0)
    assigns = [item for item in events if item["type"] == "GROUP_FLOOR" and item["payload"].get("reason") == "host_assign"]
    assert len(assigns) == 2
    second_run = assigns[-1]["payload"]["runId"]
    assert second_run != first_run
    assert assigns[-1]["payload"]["memberId"] == worker["id"]
    host_defaults = [item for item in events if item["type"] == "GROUP_FLOOR" and item["payload"].get("reason") == "host_default"]
    assert host_defaults[-1]["payload"]["runId"] == second_run
    assert get_group_studio_pending_speaker(studio["id"]) == worker["id"]


def test_pending_speaker_survives_stale_tool_context(client):
    import asyncio

    from src.group_studio.tools_host import GroupAssignFloor, HostToolContext
    from src.storage.database import get_group_studio_pending_speaker

    studio = _create_discussing(client, "sess-pending-stale")
    worker = next(item for item in studio["members"] if item["role"] == "worker")
    stale = HostToolContext(group_id=studio["id"], run_id="grun_stale", host_member_id="host")
    asyncio.run(GroupAssignFloor(stale)(memberId=worker["id"]))
    assert get_group_studio_pending_speaker(studio["id"]) == worker["id"]

    assigned = asyncio.run(run_host_discussion(studio["id"], agent_factory=lambda _spec: _TalkAgent()))
    assert assigned == worker["id"]


def test_schedule_next_starts_queued_worker(client, monkeypatch):
    import asyncio

    from src.group_studio import scheduler
    from src.storage.database import set_group_studio_pending_speaker

    studio = _create_discussing(client, "sess-pending-sched")
    worker = next(item for item in studio["members"] if item["role"] == "worker")
    called: list[str] = []

    async def fake_worker(group_id: str, member_id: str, **_kwargs) -> None:
        called.append(member_id)

    async def scenario() -> None:
        monkeypatch.setattr(scheduler, "should_auto_start_host", lambda: True)
        monkeypatch.setattr(scheduler, "_run_worker_guarded", fake_worker)
        set_group_studio_pending_speaker(studio["id"], worker["id"])
        scheduler.schedule_next(studio["id"])
        await asyncio.sleep(0.05)

    asyncio.run(scenario())
    assert called == [worker["id"]]


class _AnnounceThenExecuteAgent:
    """第一轮只口头进入执行；被 nudge 后调用 group_start_execution。"""

    def __init__(self, tools: list, member_id: str) -> None:
        self._tools = tools
        self._member_id = member_id
        self.calls = 0

    async def reply_stream(self, _message: object):
        self.calls += 1
        if self.calls == 1:
            yield _FakeEvent(type="TEXT_BLOCK_DELTA", delta="讨论结束。现在进入执行阶段：整理最终交付。")
            yield _FakeEvent(type="REPLY_END", finished_reason="completed")
            return
        tool = next(item for item in self._tools if item.name == "group_start_execution")
        await tool(memberId=self._member_id)
        yield _FakeEvent(type="TOOL_RESULT_END", tool_call_name="group_start_execution", state="success")
        yield _FakeEvent(type="REPLY_END", finished_reason="completed")


def test_host_execution_announce_nudges_start_execution(client):
    import asyncio

    from src.storage.database import get_group_studio_pending_speaker

    studio = _create_discussing(client, "sess-exec-nudge")
    worker = next(item for item in studio["members"] if item["role"] == "worker")
    held: dict[str, _AnnounceThenExecuteAgent] = {}

    def factory(spec: object) -> _AnnounceThenExecuteAgent:
        agent = _AnnounceThenExecuteAgent(list(getattr(spec, "extra_tools", []) or []), worker["id"])
        held["agent"] = agent
        return agent

    assigned = asyncio.run(run_host_discussion(studio["id"], agent_factory=factory))
    assert held["agent"].calls == 2
    assert assigned == worker["id"]
    snapshot = get_group_studio_snapshot(studio["id"])
    assert snapshot is not None
    assert snapshot["status"] == "executing"
    assert get_group_studio_pending_speaker(studio["id"]) == worker["id"]


def test_start_execution_tool_assigns_member(client):
    import asyncio

    from src.group_studio.tools_host import GroupStartExecution, HostToolContext
    from src.storage.database import get_group_studio_pending_speaker

    studio = _create_discussing(client, "sess-exec-assign")
    worker = next(item for item in studio["members"] if item["role"] == "worker")
    ctx = HostToolContext(group_id=studio["id"], run_id="grun_exec", host_member_id="host")
    result = asyncio.run(GroupStartExecution(ctx)(memberId=worker["id"]))
    assert "executing" in str(result.content)
    snapshot = get_group_studio_snapshot(studio["id"])
    assert snapshot is not None
    assert snapshot["status"] == "executing"
    assert ctx.assigned_member_id == worker["id"]
    assert get_group_studio_pending_speaker(studio["id"]) == worker["id"]


def test_scheduler_followup_host_after_execution_without_member(client, monkeypatch):
    import asyncio

    from src.group_studio import scheduler
    from src.storage.database import set_group_studio_speaker_queue, set_group_studio_status

    studio = _create_discussing(client, "sess-exec-followup")
    worker = next(item for item in studio["members"] if item["role"] == "worker")
    host_calls = {"n": 0}
    worker_calls: list[str] = []

    async def fake_host(group_id: str, agent_factory=None) -> str:
        host_calls["n"] += 1
        if host_calls["n"] == 1:
            set_group_studio_status(group_id, "executing")
            return ""
        set_group_studio_speaker_queue(group_id, [worker["id"]])
        return worker["id"]

    async def fake_worker(group_id: str, member_id: str, **_kwargs) -> None:
        worker_calls.append(member_id)

    async def scenario() -> None:
        monkeypatch.setattr(scheduler, "run_host_discussion", fake_host)
        monkeypatch.setattr(scheduler, "_run_worker_guarded", fake_worker)
        await scheduler._run_host_guarded(studio["id"])

    asyncio.run(scenario())
    assert host_calls["n"] == 2
    assert worker_calls == [worker["id"]]


def test_human_message_clears_pending_speaker(client):
    from src.storage.database import (
        get_group_studio_pending_speaker,
        set_group_studio_pending_speaker,
    )

    studio = _create_discussing(client, "sess-pending-human")
    worker = next(item for item in studio["members"] if item["role"] == "worker")
    set_group_studio_pending_speaker(studio["id"], worker["id"])
    posted = client.post(f"/api/group-studio/{studio['id']}/messages", json={
        "projectId": DEFAULT_PROJECT_ID,
        "body": "先听我的，不要继续刚才的指派",
    })
    assert posted.status_code == 200, posted.text
    assert get_group_studio_pending_speaker(studio["id"]) == ""


def test_turn_context_keeps_last_eight_visible():
    from src.group_studio.host_runtime import _host_turn_user_message
    from src.group_studio.turn_context import format_turn_context, recent_visible_messages

    messages = []
    for index in range(20):
        messages.append({
            "speakerId": "gmbr_worker",
            "role": "worker",
            "intent": "report_progress",
            "body": f"交付-{index}",
            "visibleInTimeline": index >= 10,
        })
    snapshot = {
        "status": "executing",
        "taskCard": {"goal": "写投资建议"},
        "members": [{
            "id": "gmbr_worker",
            "displayName": "投资主笔",
            "role": "worker",
            "status": "active",
            "muted": False,
            "lastSpokeAt": None,
        }],
        "messages": messages,
    }
    recent = recent_visible_messages(snapshot)
    assert [item["body"] for item in recent] == [f"交付-{index}" for index in range(12, 20)]
    text = format_turn_context(snapshot)
    assert "交付-19" in text
    assert "交付-9" not in text
    assert "从未" in text
    host_prompt = _host_turn_user_message(snapshot)
    assert "最近群消息" in host_prompt
    assert "交付-19" in host_prompt
    assert "交付-9" not in host_prompt
    assert "不要重复指派" in host_prompt


def test_group_studio_runtime_does_not_import_leader_stream():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "src" / "group_studio"
    forbidden = ("leader_stream", "PENDING_INTERACTIONS", "src.server.routes.chat.hitl")
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if any(token in text for token in forbidden):
            offenders.append(path.name)
    assert offenders == []
