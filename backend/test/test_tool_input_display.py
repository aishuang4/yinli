"""工具入参展示回归：真实 SDK 事件、并发片段、凭据脱敏、Chat replay 和 Trace。"""

import json
from types import SimpleNamespace

from agentscope.message import ToolCallBlock
from agentscope.model import ChatResponse

from src.agents.permission_adapter import PlatformAgent
from src.security.authorization.display_input import display_call_input
from src.server.routes.chat.authorization_display import public_authorization_event
from src.server.routes.chat.observation_runtime import observe_source_event, open_run_root
from src.server.routes.chat.projection.block_lifecycle import BlockLifecycleCoordinator
from src.server.routes.chat.projection.domain_events import DomainEventRepository
from src.server.routes.chat.projection.projection_registry import default_projection_registry
from src.server.routes.chat.projection.projection_runner import ProjectionRunner
from src.server.routes.chat.projection.projection_writer import ProjectionStreamStore
from src.server.routes.chat.projection.source_normalizer import ChatProjectionNormalizer
from src.server.routes.chat.tool_state import _update_tool_calls
from src.storage.database import list_trace_observations
from test.authorization_fakes import MemoryModel, MemoryTool, make_agent, run_async


def test_structured_display_preserves_business_values_and_masks_credentials():
    value = {"command": "python3 report.py --period weekly", "file_path": "notes/周报.md", "limit": 20,
             "enabled": False, "content": "联系 team@example.org", "headers": {"Authorization": "Bearer hidden"},
             "nested": [{"password": "hidden", "query": "正常查询"}],
             "url": "https://example.org/?token=hidden&query=report", "opaque": "known-provider-key"}
    result = json.loads(display_call_input(value, secrets=("known-provider-key",)))
    assert result["command"] == value["command"]
    assert result["file_path"] == value["file_path"]
    assert result["limit"] == 20 and result["enabled"] is False
    assert result["content"] == value["content"]
    assert result["nested"][0]["query"] == "正常查询"
    assert "hidden" not in json.dumps(result)
    assert "known-provider-key" not in json.dumps(result)


def test_interleaved_fragments_are_replaced_once_with_complete_safe_inputs():
    agent = SimpleNamespace(authorization_binding=object())
    for call_id in ("a", "b"):
        public_authorization_event(agent, "TOOL_CALL_START", {"tool_call_id": call_id})
    for call_id, chunk in [("a", '{"headers":{"Authorization":"Bea'), ("b", '{"file_path":"a.md","offset":10}'),
                           ("a", 'rer hidden"},"query":"report"}')]:
        assert public_authorization_event(agent, "TOOL_CALL_DELTA", {"tool_call_id": call_id, "delta": chunk})["delta"] == ""
    a = public_authorization_event(agent, "TOOL_CALL_END", {"tool_call_id": "a"})
    b = public_authorization_event(agent, "TOOL_CALL_END", {"tool_call_id": "b"})
    assert json.loads(a["input"])["query"] == "report" and "hidden" not in a["input"]
    assert json.loads(b["input"]) == {"file_path": "a.md", "offset": 10}
    assert agent._permission_display_inputs == {}


@run_async
async def test_real_sdk_tool_call_end_restores_chat_trace_history_and_replay():
    """SDK end 只有 ID；无文件/网络工具副作用，验证完整持久投影而非只测 helper。"""
    inputs = {"value": 7}

    class ToolModel(MemoryModel):
        async def _call_api(self, model, **kwargs):
            return ChatResponse(content=[ToolCallBlock(id="sdk-call", name="MemoryTool", input=json.dumps(inputs))], is_last=True)

    log = []
    agent = make_agent(MemoryTool(log), [], PlatformAgent, authorization_service=object(), action_builder=object())
    agent.model = ToolModel()
    agent.authorization_binding = SimpleNamespace(action_builder=None)
    events = DomainEventRepository()
    store = ProjectionStreamStore()
    coordinator = BlockLifecycleCoordinator()
    runner = ProjectionRunner(events=events, store=store, registry=default_projection_registry(), coordinator=coordinator)
    normalizer = ChatProjectionNormalizer(stream_key="stream-input", run_id="input-run", session_id="input-session", events=events)
    normalizer.start(agent_name="测试")
    open_run_root(persist=True, run_id="input-run", session_id="input-session", task="验证工具参数")
    calls = []
    saw_end = False
    async for event in agent._reasoning_impl():
        if not hasattr(event, "model_dump"):
            continue
        raw = event.model_dump(mode="json")
        kind = str(raw.pop("type", ""))
        if kind == "TOOL_CALL_END":
            assert "input" not in raw
            saw_end = True
        data = public_authorization_event(agent, kind, raw)
        calls = _update_tool_calls(calls, kind, data)
        normalizer.ingest(kind, data)
        observe_source_event(persist=True, run_id="input-run", session_id="input-session", event_type=kind, data=data)
    assert saw_end and log == []
    assert json.loads(calls[0].args) == inputs
    normalizer.ingest("TOOL_RESULT_END", {"tool_call_id": "sdk-call", "state": "success"})
    normalizer.ingest("RUN_COMPLETED", {})
    runner.run_available("stream-input", "input-run")
    snapshot = store.load_materialized("stream-input", coordinator).snapshot
    assert json.loads(snapshot.blocks_by_id["tool:sdk-call"].data["inputPreview"]) == inputs
    store.compact("stream-input")
    replay = store.load_materialized("stream-input", coordinator).snapshot
    assert replay.blocks_by_id["tool:sdk-call"].data == snapshot.blocks_by_id["tool:sdk-call"].data
    observation = next(row for row in list_trace_observations("input-run") if row["kind"] == "tool")
    assert observation["input"] == inputs
