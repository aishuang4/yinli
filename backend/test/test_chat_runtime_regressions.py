import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from pydantic import ValidationError

from src.server.routes.chat import background_runs as background_runs_module
from src.server.routes.chat import store as chat_store_module
from src.server.routes.chat.background_runs import (
    ChatBackgroundRun,
    mark_chat_background_run_resuming,
    relay_chat_background_run_events,
    start_chat_background_run,
    subscribe_chat_background_run,
)
from src.server.routes.chat.helpers import source_event
from src.server.routes.chat.projection.source_events import SourceEvent
from src.server.routes.chat.hitl import PENDING_AGENTS, PENDING_INTERACTIONS, _build_resume_event, _create_pending_interaction
from src.server.routes.chat.handlers.resume import (
    _is_final_answer_text_scope,
    _should_emit_raw_resume_event,
    _should_wrap_resume_text_as_final,
    resume_interaction_events,
)
from src.server.routes.chat.handlers.runs import _build_rerun_chat_message
from src.server.routes.chat.intent_router import (
    IntentRoute,
    route_chat_message,
    route_chat_message_with_model,
    route_trace_payload,
)
from src.agents.session_context import SessionHistoryMessage
from src.server.routes.chat.models import (
    ChatFilePayload,
    ChatMessage,
    ChatResumeRequest,
    ChatToolRun,
)
from src.server.routes.chat.leader_stream import LeaderStreamState, stream_leader_agent_reply
from src.server.routes.chat.final_answer_recovery import _execute_recovery_tool_calls
from src.server.routes.chat.prompts import _build_final_answer_system_prompt
from src.server.routes.chat.runtime import _permission_mode_for_guardrails
from src.server.routes.chat.react_runtime import execute_react_runtime
from src.server.routes.chat.run_context import ChatRunContext
from src.server.routes.chat.run_context import build_chat_run_context
from src.agents.factory import _safe_workspace_dir
from src.agents.runtime_types import WORKSPACE_DIR
from src.storage.database import _normalize_guardrails
from src.server.routes.chat.runtime_builder import (
    RuntimeModelConfig,
    build_main_agent_runtime_spec,
    build_runtime_agent_cache_key,
    build_runtime_model_config,
    create_main_agent_from_spec,
)
from src.server.routes.chat.runtime_events import runtime_event_from_agentscope_event, runtime_trace_source_event
from src.server.routes.chat.tool_scope_resolver import resolve_runtime_tool_scope
from src.server.routes.chat.tool_state import _pending_tool_calls_to_runs
from src.server.routes.chat.trace_recorder import _model_cache_from_event, _permission_decision_payload
from src.agents.factory import AgentGuardrails, AgentModelOptions, RuntimeToolScope, _build_context_config
from src.security.permissions import (
    PermissionAction,
    PermissionDecision,
    ResourceType,
    build_permission_profile,
)
from src.tools.ask_user import AskUser
from src.server.routes.agent_builder import _default_agent_prompt
from src.runtime_context import RuntimeContext


def _test_runtime_context(session_id: str = "session-1", agent_id: str = "agent-1") -> RuntimeContext:
    """构造不依赖持久化 Project 的测试运行上下文。"""

    return RuntimeContext(
        workspace_id="default",
        project_id="default",
        project_root=Path(__file__).resolve().parents[2],
        session_id=session_id,
        run_id="run-test",
        agent_id=agent_id,
        project_name="Default Project",
    )


class _FakeAgentScopeEvent:
    def __init__(self, payload: dict):
        self.payload = payload

    def model_dump(self, mode: str = "json") -> dict:
        return dict(self.payload)


def _sse_payload(event: SourceEvent) -> dict:
    return event.data


def _event_is(event: SourceEvent, event_type: str) -> bool:
    return event.event_type == event_type


def _event_contains(event: SourceEvent, value: str) -> bool:
    return value in event.event_type or value in json.dumps(event.data, ensure_ascii=False, default=str)


def _final_answer_deltas(events: list[SourceEvent]) -> list[str]:
    return [
        str(_sse_payload(event).get("delta") or "")
        for event in events
        if _event_is(event, "FINAL_ANSWER_DELTA")
    ]


def test_final_answer_prompt_requires_phase_marker_for_streaming():
    prompt = _build_final_answer_system_prompt()
    assert "FinalAnswerStart" in prompt
    assert "required phase marker" in prompt
    # 直接回答不要求标记，用过工具后才要求
    assert "Do NOT call FinalAnswerStart for such direct answers" in prompt
    assert "Once you have called any business tool or AskUser in this turn" in prompt
    assert "after a tool or AskUser resumes" in prompt


def test_direct_answer_streams_before_reply_end_when_no_tool_call():
    """本次 reply 无工具调用且文本超过阈值时，提前把累计文本投影为最终回答。"""

    from src.server.routes.chat import leader_stream

    state = LeaderStreamState()
    state.pending_text = "答" * (leader_stream.DIRECT_ANSWER_STREAM_THRESHOLD - 1)
    assert leader_stream._should_stream_direct_answer(state, []) is False
    state.pending_text += "案"
    assert leader_stream._should_stream_direct_answer(state, []) is True

    tooling = LeaderStreamState(reply_had_tool_call=True)
    tooling.pending_text = "过程说明" * 400
    assert leader_stream._should_stream_direct_answer(tooling, []) is False

    blocked = LeaderStreamState(subagent_completion_blocked_reason="attached task pending")
    blocked.pending_text = "文" * 1000
    assert leader_stream._should_stream_direct_answer(blocked, []) is False


def test_default_agent_prompt_mentions_final_answer_marker():
    prompt = _default_agent_prompt("Assistant", "Help the user.")
    assert "FinalAnswerStart" in prompt


def _projection_run() -> ChatBackgroundRun:
    return ChatBackgroundRun(
        id="run-projection-1",
        session_id="session-projection-1",
        agent_id="agent-1",
        agent_name="Agent",
        task_text="测试投影",
    )


class _FakeHitlAgent:
    async def reply_stream(self, user_msg):
        yield _FakeAgentScopeEvent({
            "type": "REQUIRE_USER_CONFIRM",
            "reply_id": "reply-1",
            "tool_calls": [{
                "id": "call-read",
                "name": "Read",
                "input": '{"file_path":"docs/chat-sse-event-contract.md"}',
                "state": "asking",
            }],
        })


class _FakeDelegateTaskParentAgent:
    def __init__(self) -> None:
        self.resume_events: list[object] = []

    async def reply_stream(self, user_msg):
        from agentscope.event import ExternalExecutionResultEvent

        if isinstance(user_msg, ExternalExecutionResultEvent):
            self.resume_events.append(user_msg)
            yield _FakeAgentScopeEvent({
                "type": "TOOL_CALL_START",
                "reply_id": "reply-parent",
                "tool_call_id": "final-1",
                "tool_call_name": "FinalAnswerStart",
            })
            yield _FakeAgentScopeEvent({
                "type": "TEXT_BLOCK_DELTA",
                "reply_id": "reply-parent",
                "block_id": "text-1",
                "delta": "父 Agent 已继续。",
            })
            yield _FakeAgentScopeEvent({
                "type": "REPLY_END",
                "session_id": "session-delegate",
                "reply_id": "reply-parent",
            })
            return
        yield _FakeAgentScopeEvent({
            "type": "REQUIRE_EXTERNAL_EXECUTION",
            "reply_id": "reply-parent",
            "tool_calls": [{
                "type": "tool_call",
                "id": "delegate-1",
                "name": "DelegateTask",
                "input": '{"subagentName":"explorer","task":"查找相关文件"}',
                "state": "submitted",
            }],
        })


class _FakeParallelDelegateTaskParentAgent(_FakeDelegateTaskParentAgent):
    async def reply_stream(self, user_msg):
        from agentscope.event import ExternalExecutionResultEvent

        if isinstance(user_msg, ExternalExecutionResultEvent):
            self.resume_events.append(user_msg)
            yield _FakeAgentScopeEvent({
                "type": "TOOL_CALL_START",
                "reply_id": "reply-parallel",
                "tool_call_id": "final-parallel",
                "tool_call_name": "FinalAnswerStart",
            })
            yield _FakeAgentScopeEvent({
                "type": "TEXT_BLOCK_DELTA",
                "reply_id": "reply-parallel",
                "block_id": "text-parallel",
                "delta": "两个结果均已收到。",
            })
            yield _FakeAgentScopeEvent({
                "type": "REPLY_END",
                "session_id": "session-parallel-delegate",
                "reply_id": "reply-parallel",
            })
            return
        for index in range(2):
            yield _FakeAgentScopeEvent({
                "type": "REQUIRE_EXTERNAL_EXECUTION",
                "reply_id": "reply-parallel",
                "tool_calls": [{
                    "type": "tool_call",
                    "id": f"delegate-{index + 1}",
                    "name": "DelegateTask",
                    "input": (
                        '{"subagentName":"explorer","task":"查找文件 '
                        f'{index + 1}'
                        '"}'
                    ),
                    "state": "submitted",
                }],
            })


class _FakeSubagentExternalManager:
    def __init__(self) -> None:
        self.called_tool_ids: list[str] = []

    async def execute_external_tool_call(self, tool_call):
        from agentscope.message import TextBlock, ToolResultBlock, ToolResultState

        self.called_tool_ids.append(str(tool_call.get("id") or ""))
        return ToolResultBlock(
            id=str(tool_call.get("id") or ""),
            name="DelegateTask",
            output=[TextBlock(text="Subagent explorer completed.\n\nSummary:\n找到 README.md。")],
            state=ToolResultState.SUCCESS,
        )

    def drain_runtime_events(self):
        return [{
            "type": "SUBAGENT_COMPLETED",
            "status": "completed",
            "scope": "subagent",
            "payload": {
                "invocationId": "invocation-1",
                "toolCallId": "delegate-1",
                "subagentName": "explorer",
                "resultPreview": "找到 README.md。",
            },
        }]


class _FakeMultiToolHitlAgent:
    async def reply_stream(self, user_msg):
        yield _FakeAgentScopeEvent({
            "type": "TOOL_CALL_START",
            "reply_id": "reply-1",
            "tool_call_id": "call-read-1",
            "tool_call_name": "Read",
        })
        yield _FakeAgentScopeEvent({
            "type": "TOOL_CALL_DELTA",
            "reply_id": "reply-1",
            "tool_call_id": "call-read-1",
            "delta": '{"file_path":"a.py"}',
        })
        yield _FakeAgentScopeEvent({
            "type": "TOOL_CALL_END",
            "reply_id": "reply-1",
            "tool_call_id": "call-read-1",
        })
        yield _FakeAgentScopeEvent({
            "type": "TOOL_CALL_START",
            "reply_id": "reply-1",
            "tool_call_id": "call-read-2",
            "tool_call_name": "Read",
        })
        yield _FakeAgentScopeEvent({
            "type": "TOOL_CALL_DELTA",
            "reply_id": "reply-1",
            "tool_call_id": "call-read-2",
            "delta": '{"file_path":"b.py"}',
        })
        yield _FakeAgentScopeEvent({
            "type": "TOOL_CALL_END",
            "reply_id": "reply-1",
            "tool_call_id": "call-read-2",
        })
        yield _FakeAgentScopeEvent({
            "type": "REQUIRE_USER_CONFIRM",
            "reply_id": "reply-1",
            "tool_calls": [{
                "type": "tool_call",
                "id": "call-read-1",
                "name": "Read",
                "input": '{"file_path":"a.py"}',
                "state": "asking",
            }],
        })
        yield _FakeAgentScopeEvent({
            "type": "REQUIRE_USER_CONFIRM",
            "reply_id": "reply-1",
            "tool_calls": [{
                "type": "tool_call",
                "id": "call-read-2",
                "name": "Read",
                "input": '{"file_path":"b.py"}',
                "state": "asking",
            }],
        })


class _FakeResumeFinalAnswerAgent:
    async def reply_stream(self, resume_event):
        yield _FakeAgentScopeEvent({
            "type": "TEXT_BLOCK_DELTA",
            "reply_id": "reply-1",
            "delta": "过程文本",
        })
        yield _FakeAgentScopeEvent({
            "type": "TOOL_CALL_START",
            "reply_id": "reply-1",
            "tool_call_id": "call-final",
            "tool_call_name": "FinalAnswerStart",
        })
        yield _FakeAgentScopeEvent({
            "type": "TOOL_CALL_END",
            "reply_id": "reply-1",
            "tool_call_id": "call-final",
        })
        yield _FakeAgentScopeEvent({
            "type": "TOOL_RESULT_START",
            "reply_id": "reply-1",
            "tool_call_id": "call-final",
            "tool_call_name": "FinalAnswerStart",
        })
        yield _FakeAgentScopeEvent({
            "type": "TOOL_RESULT_END",
            "reply_id": "reply-1",
            "tool_call_id": "call-final",
            "state": "success",
        })
        yield _FakeAgentScopeEvent({
            "type": "TEXT_BLOCK_DELTA",
            "reply_id": "reply-1",
            "delta": "最终文本",
        })
        yield _FakeAgentScopeEvent({
            "type": "REPLY_END",
            "reply_id": "reply-1",
        })


class _FakeNoFinalAnswerAgent:
    async def reply_stream(self, user_msg):
        yield _FakeAgentScopeEvent({
            "type": "TEXT_BLOCK_DELTA",
            "reply_id": "reply-no-final",
            "delta": "只应该作为过程展示",
        })
        yield _FakeAgentScopeEvent({
            "type": "REPLY_END",
            "reply_id": "reply-no-final",
        })


class _FakeRecoverableNoFinalAnswerAgent:
    def __init__(self):
        self.state = SimpleNamespace(reply_id="", cur_iter=0)

    async def reply_stream(self, user_msg):
        yield _FakeAgentScopeEvent({
            "type": "TEXT_BLOCK_DELTA",
            "reply_id": "reply-no-final",
            "delta": "工具后的过程总结",
        })
        yield _FakeAgentScopeEvent({
            "type": "REPLY_END",
            "reply_id": "reply-no-final",
        })

    async def _handle_incoming_messages(self, msgs):
        self.recovery_message = msgs

    async def _reasoning(self, tool_choice=None):
        if getattr(tool_choice, "mode", "") == "FinalAnswerStart":
            yield _FakeAgentScopeEvent({
                "type": "TOOL_CALL_START",
                "reply_id": "reply-recovery-marker",
                "tool_call_id": "call-recovery-final",
                "tool_call_name": "FinalAnswerStart",
            })
            yield _FakeAgentScopeEvent({
                "type": "TOOL_CALL_END",
                "reply_id": "reply-recovery-marker",
                "tool_call_id": "call-recovery-final",
            })
            return
        if getattr(tool_choice, "mode", "") == "none":
            yield _FakeAgentScopeEvent({
                "type": "TEXT_BLOCK_DELTA",
                "reply_id": "reply-recovery-answer",
                "delta": "恢复后的最终答案",
            })
            yield _FakeAgentScopeEvent({
                "type": "REPLY_END",
                "reply_id": "reply-recovery-answer",
            })

    def _get_last_msg(self):
        tool_calls = [SimpleNamespace(id="call-recovery-final")]
        return SimpleNamespace(get_content_blocks=lambda block_type: tool_calls if block_type == "tool_call" else [])

    async def _batch_tool_calls(self, tool_calls):
        return [SimpleNamespace(type="concurrent", tool_calls=tool_calls)]

    async def _execute_concurrent_tool_calls(self, tool_calls):
        yield _FakeAgentScopeEvent({
            "type": "TOOL_RESULT_START",
            "reply_id": "reply-recovery-marker",
            "tool_call_id": "call-recovery-final",
            "tool_call_name": "FinalAnswerStart",
        })
        yield _FakeAgentScopeEvent({
            "type": "TOOL_RESULT_END",
            "reply_id": "reply-recovery-marker",
            "tool_call_id": "call-recovery-final",
            "state": "success",
        })


class _FakeExceedMaxItersAgent:
    async def reply_stream(self, user_msg):
        yield _FakeAgentScopeEvent({
            "type": "TEXT_BLOCK_DELTA",
            "reply_id": "reply-max-iters",
            "delta": "还在继续分析",
        })
        yield _FakeAgentScopeEvent({
            "type": "EXCEED_MAX_ITERS",
            "reply_id": "reply-max-iters",
            "name": "Agent",
        })


class _FakeRecoverableExceedMaxItersAgent(_FakeRecoverableNoFinalAnswerAgent):
    async def reply_stream(self, user_msg):
        yield _FakeAgentScopeEvent({
            "type": "TEXT_BLOCK_DELTA",
            "reply_id": "reply-max-iters-recoverable",
            "delta": "达到迭代上限前的过程总结",
        })
        yield _FakeAgentScopeEvent({
            "type": "EXCEED_MAX_ITERS",
            "reply_id": "reply-max-iters-recoverable",
            "name": "Agent",
        })


class _FakeRecoverableBareExceedMaxItersAgent(_FakeRecoverableNoFinalAnswerAgent):
    async def reply_stream(self, user_msg):
        yield _FakeAgentScopeEvent({
            "type": "EXCEED_MAX_ITERS",
            "reply_id": "reply-max-iters-bare",
            "name": "Agent",
        })


class _FakePlanReviewAgent:
    plan_markdown = "# 计划\n\n- [ ] 修改文件\n- [ ] 验证前端流式输出\n- [ ] 总结结果"

    async def reply_stream(self, user_msg):
        yield _FakeAgentScopeEvent({
            "type": "TOOL_CALL_START",
            "reply_id": "reply-1",
            "tool_call_id": "call-plan-review",
            "tool_call_name": "plan_request_review",
        })
        yield _FakeAgentScopeEvent({
            "type": "TOOL_RESULT_START",
            "reply_id": "reply-1",
            "tool_call_id": "call-plan-review",
            "tool_call_name": "plan_request_review",
        })
        yield _FakeAgentScopeEvent({
            "type": "TOOL_RESULT_TEXT_DELTA",
            "reply_id": "reply-1",
            "tool_call_id": "call-plan-review",
            "delta": json.dumps({
                "planId": "plan-1",
                "planFile": ".agent-workstation/plans/session-1/plan-1.md",
                "markdown": self.plan_markdown,
            }, ensure_ascii=False),
        })
        yield _FakeAgentScopeEvent({
            "type": "TOOL_RESULT_END",
            "reply_id": "reply-1",
            "tool_call_id": "call-plan-review",
            "state": "success",
        })
        yield _FakeAgentScopeEvent({
            "type": "REPLY_END",
            "reply_id": "reply-1",
        })


class _FakeNativePlanReviewManager:
    """模拟 plan_request_review 产出审核事件的真实路径。"""

    def __init__(self) -> None:
        self.drain_count = 0

    def drain_runtime_events(self) -> list[dict]:
        self.drain_count += 1
        if self.drain_count != 3:
            return []
        return [{
            "id": "runtime-plan-finalized",
            "runId": "run-1",
            "sessionId": "session-1",
            "type": "PLAN_REVIEW_REQUESTED",
            "source": "runtime",
            "sourceEventType": None,
            "scope": "plan",
            "agentId": "agent-1",
            "agentName": "Agent",
            "status": "waiting",
            "reason": "",
            "timestamp": "2026-07-01T00:00:00+00:00",
            "durationMs": None,
            "payload": {
                "planId": "plan-1",
                "planFile": ".agent-workstation/plans/session-1/plan-1.md",
                "markdown": _FakePlanReviewAgent.plan_markdown,
            },
        }]


class _FakePlanReviewThenContinuesAgent(_FakePlanReviewAgent):
    async def reply_stream(self, user_msg):
        async for event in super().reply_stream(user_msg):
            yield event
        yield _FakeAgentScopeEvent({
            "type": "TEXT_BLOCK_DELTA",
            "reply_id": "reply-1",
            "delta": "不应该继续输出",
        })
        yield _FakeAgentScopeEvent({
            "type": "TOOL_CALL_START",
            "reply_id": "reply-1",
            "tool_call_id": "call-write-after-plan-review",
            "tool_call_name": "Write",
        })


def test_model_cache_from_event_ignores_non_cache_usage():
    cache = _model_cache_from_event(
        {
            "id": "evt-1",
            "input_tokens": 100,
            "output_tokens": 20,
            "cache_creation_input_tokens": 0,
            "cache_input_tokens": 0,
            "created_at": "2026-06-18T00:00:00",
        },
        "deepseek-chat",
    )

    assert cache is None


def test_natural_language_clarification_is_detected():
    """模型误把反问当最终答案时，后端必须识别为用户补充信息。"""

    from src.server.routes.chat.prompts import _extract_clarification_request

    clarification = _extract_clarification_request(
        "你好！请问你具体想让我处理什么？请描述一下你要处理的具体内容，我来帮你。"
    )

    assert clarification is not None
    assert clarification["kind"] == "clarification"
    assert clarification["clearFinalAnswer"] is True
    assert clarification["questions"][0]["id"] == "target"


def test_readiness_gate_normalizes_clarification_identity():
    """Readiness 追问必须带稳定身份，才能被前端识别为 clarification。"""

    from src.server.routes.chat.readiness_gate import _normalize_clarification

    clarification = _normalize_clarification({
        "title": "需要补充信息",
        "description": "请选择目标文件",
        "questions": [{
            "id": "target",
            "type": "text",
            "label": "目标文件",
        }],
    })

    assert clarification is not None
    assert clarification["kind"] == "clarification"
    assert clarification["type"] == "clarification"
    assert clarification["id"] == clarification["interactionId"]
    assert clarification["questions"][0]["id"] == "target"


def test_readiness_gate_routes_missing_details_to_runtime_ask_user():
    """业务信息缺口不再由 Readiness 反问，交给 Runtime AskUser 暂停恢复。"""

    from src.server.routes.chat.readiness_gate import check_message_readiness_by_rule

    decision = check_message_readiness_by_rule("帮我修改一下")

    assert decision.ready is True
    assert decision.clarification is None


def test_external_execution_resume_uses_text_block_output():
    """AskUser 恢复必须把用户答案作为 AgentScope 外部工具结果注入原 reply。"""

    from agentscope.event import ExternalExecutionResultEvent
    from src.server.routes.chat.hitl import _build_resume_event
    from src.server.routes.chat.models import ChatResumeRequest, PendingInteraction

    event = _build_resume_event(
        PendingInteraction(
            id="interaction-1",
            type="REQUIRE_EXTERNAL_EXECUTION",
            session_id="session-1",
            agent_id="agent-1",
            agent_name="Agent",
            model_name="model",
            reply_id="reply-1",
            tool_calls=[{
                "type": "tool_call",
                "id": "call-1",
                "name": "AskUser",
                "input": "{}",
            }],
            message="帮我修改一下",
        ),
        ChatResumeRequest(
            interactionId="interaction-1",
            action="approve",
            externalResults={"call-1": "目标是 README.md"},
        ),
    )

    assert isinstance(event, ExternalExecutionResultEvent)
    result = event.execution_results[0]
    assert result.id == "call-1"
    assert result.output[0].text == "目标是 README.md"


def test_tool_result_runtime_event_preserves_failed_state():
    """工具结果失败时，流式 Runtime Event 应立即标记 failed。"""

    raw = runtime_event_from_agentscope_event(
        "TOOL_RESULT_END",
        session_id="session-1",
        scope="single",
        agent_id="agent-1",
        agent_name="Agent",
        event_data={
            "tool_call_id": "call-edit",
            "tool_call_name": "Edit",
            "state": "error",
        },
    )

    assert raw is not None
    event = raw.data
    assert event["type"] == "TOOL_RESULT_COMPLETED"
    assert event["status"] == "failed"
    assert event["payload"]["state"] == "error"


def test_reasoning_is_flushed_before_each_tool_call():
    """多轮 ReAct 必须在每次工具调用前产出独立 reasoning，不能拖到整轮 REPLY_END。"""

    class FakeMultiStepAgent:
        async def reply_stream(self, _user_msg):
            for payload in (
                {"type": "REPLY_START", "reply_id": "reply-1"},
                {"type": "TEXT_BLOCK_DELTA", "reply_id": "reply-1", "delta": "先检查配置。"},
                {"type": "TOOL_CALL_START", "reply_id": "reply-1", "tool_call_id": "call-1", "tool_call_name": "Read"},
                {"type": "TOOL_CALL_END", "reply_id": "reply-1", "tool_call_id": "call-1"},
                {"type": "TOOL_RESULT_END", "reply_id": "reply-1", "tool_call_id": "call-1", "state": "success"},
                {"type": "TEXT_BLOCK_DELTA", "reply_id": "reply-1", "delta": "再搜索引用。"},
                {"type": "TOOL_CALL_START", "reply_id": "reply-1", "tool_call_id": "call-2", "tool_call_name": "Grep"},
                {"type": "TOOL_CALL_END", "reply_id": "reply-1", "tool_call_id": "call-2"},
                {"type": "TOOL_RESULT_END", "reply_id": "reply-1", "tool_call_id": "call-2", "state": "success"},
                {"type": "TOOL_CALL_START", "reply_id": "reply-1", "tool_call_id": "final-1", "tool_call_name": "FinalAnswerStart"},
                {"type": "TEXT_BLOCK_DELTA", "reply_id": "reply-1", "delta": "检查完成。"},
                {"type": "REPLY_END", "reply_id": "reply-1"},
            ):
                yield _FakeAgentScopeEvent(payload)

    async def run_case():
        state = LeaderStreamState()
        events = [
            event
            async for event in stream_leader_agent_reply(
                agent=FakeMultiStepAgent(),
                user_msg=object(),
                state=state,
                session_id="session-reasoning-segments",
                persist=False,
                agent_id="agent-1",
                agent_name="Agent",
                model_name="deepseek-chat",
                guardrails=AgentGuardrails(),
                message="检查项目",
                stream_scope="single",
                skill_runs=[],
            )
        ]
        return state, events

    state, events = asyncio.run(run_case())
    application_events = [
        event
        for event in events
        if event.event_type in {"REASONING_DELTA", "TOOL_CALL_START", "FINAL_ANSWER_DELTA"}
    ]

    assert [(event.event_type, event.data.get("delta") or event.data.get("tool_call_id")) for event in application_events] == [
        ("REASONING_DELTA", "先检查配置。"),
        ("TOOL_CALL_START", "call-1"),
        ("REASONING_DELTA", "再搜索引用。"),
        ("TOOL_CALL_START", "call-2"),
        ("FINAL_ANSWER_DELTA", "检查完成。"),
    ]
    assert state.assistant_reasoning_text == "先检查配置。再搜索引用。"
    assert state.assistant_text == "检查完成。"


def test_deep_thinking_progress_before_tool_stays_out_of_final_answer():
    """thinking 后的过程文字仍可能接工具，不能提前进入最终正文。"""

    class FakeDeepThinkingAgent:
        async def reply_stream(self, _user_msg):
            for payload in (
                {"type": "REPLY_START", "reply_id": "reply-thinking"},
                {"type": "THINKING_BLOCK_DELTA", "reply_id": "reply-thinking", "delta": "分析中。"},
                {"type": "THINKING_BLOCK_END", "reply_id": "reply-thinking"},
                {"type": "TEXT_BLOCK_DELTA", "reply_id": "reply-thinking", "delta": "我先执行任务。"},
                {"type": "TOOL_CALL_START", "reply_id": "reply-thinking", "tool_call_id": "call-bash", "tool_call_name": "Bash"},
                {"type": "TOOL_CALL_END", "reply_id": "reply-thinking", "tool_call_id": "call-bash"},
                {"type": "TOOL_RESULT_END", "reply_id": "reply-thinking", "tool_call_id": "call-bash", "state": "success"},
                {"type": "TOOL_CALL_START", "reply_id": "reply-thinking", "tool_call_id": "call-final", "tool_call_name": "FinalAnswerStart"},
                {"type": "TEXT_BLOCK_DELTA", "reply_id": "reply-thinking", "delta": "第一段"},
                {"type": "TEXT_BLOCK_DELTA", "reply_id": "reply-thinking", "delta": "第二段"},
                {"type": "REPLY_END", "reply_id": "reply-thinking"},
            ):
                yield _FakeAgentScopeEvent(payload)

    async def run_case():
        state = LeaderStreamState()
        events = [
            event
            async for event in stream_leader_agent_reply(
                agent=FakeDeepThinkingAgent(),
                user_msg=object(),
                state=state,
                session_id="session-deep-thinking-stream",
                persist=False,
                agent_id="agent-1",
                agent_name="Agent",
                model_name="deepseek-reasoner",
                guardrails=AgentGuardrails(),
                message="深度分析后回答",
                stream_scope="single",
                skill_runs=[],
                deep_thinking=True,
            )
        ]
        return state, events

    state, events = asyncio.run(run_case())

    assert _final_answer_deltas(events) == ["第一段", "第二段"]
    assert state.assistant_thinking == "分析中。"
    assert state.assistant_text == "第一段第二段"
    assert state.assistant_reasoning_text == "我先执行任务。"
    assert sum(
        1
        for event in events
        if _event_is(event, "AGENT_FINAL_ANSWER_STARTED")
    ) == 1


def test_thinking_events_do_not_stream_direct_answer_when_mode_is_disabled():
    """未显式开启深度思考时，即使 provider 发 thinking，也沿用直接回答终态判定。"""

    class FakeUnexpectedThinkingAgent:
        async def reply_stream(self, _user_msg):
            for payload in (
                {"type": "REPLY_START", "reply_id": "reply-thinking-disabled"},
                {"type": "THINKING_BLOCK_DELTA", "reply_id": "reply-thinking-disabled", "delta": "分析中。"},
                {"type": "TEXT_BLOCK_DELTA", "reply_id": "reply-thinking-disabled", "delta": "第一段"},
                {"type": "TEXT_BLOCK_DELTA", "reply_id": "reply-thinking-disabled", "delta": "第二段"},
                {"type": "REPLY_END", "reply_id": "reply-thinking-disabled"},
            ):
                yield _FakeAgentScopeEvent(payload)

    async def run_case():
        return [
            event
            async for event in stream_leader_agent_reply(
                agent=FakeUnexpectedThinkingAgent(),
                user_msg=object(),
                state=LeaderStreamState(),
                session_id="session-thinking-disabled",
                persist=False,
                agent_id="agent-1",
                agent_name="Agent",
                model_name="deepseek-chat",
                guardrails=AgentGuardrails(),
                message="回答",
                stream_scope="single",
                skill_runs=[],
            )
        ]

    events = asyncio.run(run_case())

    assert _final_answer_deltas(events) == ["第一段第二段"]


def test_single_agent_hitl_pending_interaction_keeps_stream_scope():
    """单 Agent 人工审批恢复必须保留 scope 与本轮 Runtime 增强状态。"""

    async def run_case():
        PENDING_INTERACTIONS.clear()
        PENDING_AGENTS.clear()
        plan_mode_manager = SimpleNamespace(drain_runtime_events=lambda: [])
        runtime_event_source = SimpleNamespace(drain_runtime_events=lambda: [])
        events = [
            event
            async for event in stream_leader_agent_reply(
                agent=_FakeHitlAgent(),
                user_msg=object(),
                state=LeaderStreamState(),
                session_id="session-single-hitl",
                persist=False,
                agent_id="agent-1",
                agent_name="Agent",
                model_name="deepseek-chat",
                guardrails=[],
                message="读取文件",
                stream_scope="single",
                skill_runs=[],
                plan_mode_manager=plan_mode_manager,
                runtime_event_sources=[runtime_event_source],
            )
        ]
        return events, list(PENDING_INTERACTIONS.values()), plan_mode_manager, runtime_event_source

    events, interactions, plan_mode_manager, runtime_event_source = asyncio.run(run_case())

    try:
        assert interactions
        assert interactions[0].scope == "single"
        assert interactions[0].plan_mode_manager is plan_mode_manager
        assert interactions[0].runtime_event_sources == [runtime_event_source]
        assert any(
            event.data.get("scope") == "single"
            for event in events
            if _event_is(event, "HITL_REQUIRED")
        )
    finally:
        PENDING_INTERACTIONS.clear()
        PENDING_AGENTS.clear()


def test_permission_resume_continues_plan_runtime_events():
    """权限审批恢复后必须继续投递 Plan 步骤事件，避免进度面板停在旧步骤。"""

    class FakePlanManager:
        def __init__(self) -> None:
            self.events: list[dict] = []

        def drain_runtime_events(self) -> list[dict]:
            events = list(self.events)
            self.events.clear()
            return events

        def run_completion_error(self) -> str:
            return ""

        def fail_run(self, *, reason: str) -> None:
            del reason

    class FakePlanResumeAgent:
        def __init__(self, manager: FakePlanManager) -> None:
            self.manager = manager

        async def reply_stream(self, _resume_event):
            self.manager.events.append({
                "type": "PLAN_STEP_UPDATED",
                "status": "completed",
                "payload": {
                    "plan": {
                        "planId": "plan-resume",
                        "status": "executing",
                        "steps": [{"id": "step-2", "status": "completed"}],
                    },
                },
            })
            for payload in (
                {
                    "type": "TOOL_RESULT_END",
                    "reply_id": "reply-plan-resume",
                    "tool_call_id": "call-write",
                    "tool_call_name": "Write",
                    "state": "success",
                },
                {
                    "type": "TOOL_CALL_START",
                    "reply_id": "reply-plan-resume",
                    "tool_call_id": "final-plan-resume",
                    "tool_call_name": "FinalAnswerStart",
                },
                {
                    "type": "TEXT_BLOCK_DELTA",
                    "reply_id": "reply-plan-resume",
                    "delta": "计划执行完成。",
                },
                {
                    "type": "REPLY_END",
                    "reply_id": "reply-plan-resume",
                    "finished_reason": "completed",
                },
            ):
                yield _FakeAgentScopeEvent(payload)

    async def run_case():
        PENDING_INTERACTIONS.clear()
        PENDING_AGENTS.clear()
        manager = FakePlanManager()
        interaction = _create_pending_interaction(
            interaction_type="REQUIRE_USER_CONFIRM",
            agent=FakePlanResumeAgent(manager),
            session_id="session-plan-permission-resume",
            agent_id="agent-1",
            agent_name="Agent",
            model_name="deepseek-chat",
            reply_id="reply-plan-resume",
            tool_calls=[{
                "id": "call-write",
                "name": "Write",
                "input": '{"file_path":"artifacts/summary.md","content":"summary"}',
                "state": "asking",
            }],
            message="执行已审批计划",
            assistant_text="",
            skill_runs=[],
            scope="single_agent",
            plan_mode_manager=manager,
            persist=False,
        )
        stream = resume_interaction_events(
            ChatResumeRequest.model_validate({
                "interactionId": interaction.id,
                "action": "approve",
            })
        )
        return [event async for event in stream]

    events = asyncio.run(run_case())

    try:
        plan_events = [event for event in events if _event_is(event, "PLAN_STEP_UPDATED")]
        assert len(plan_events) == 1
        assert plan_events[0].data["payload"]["plan"]["steps"][0]["status"] == "completed"
        assert _final_answer_deltas(events) == ["计划执行完成。"]
    finally:
        PENDING_INTERACTIONS.clear()
        PENDING_AGENTS.clear()


def test_delegate_task_external_result_resumes_parent_agent():
    """DelegateTask 作为外部工具完成后，应把结果喂回父 Agent 并继续 ReAct。"""

    async def run_case():
        agent = _FakeDelegateTaskParentAgent()
        manager = _FakeSubagentExternalManager()
        state = LeaderStreamState()
        events = [
            event
            async for event in stream_leader_agent_reply(
                agent=agent,
                user_msg=object(),
                state=state,
                session_id="session-delegate",
                persist=False,
                agent_id="agent-1",
                agent_name="Agent",
                model_name="deepseek-chat",
                guardrails=[],
                message="委托探索",
                stream_scope="single",
                skill_runs=[],
                runtime_event_sources=[manager],
            )
        ]
        return events, agent, manager, state

    events, agent, manager, state = asyncio.run(run_case())

    assert manager.called_tool_ids == ["delegate-1"]
    assert agent.resume_events
    assert agent.resume_events[0].execution_results[0].id == "delegate-1"
    assert "Subagent explorer completed" in agent.resume_events[0].execution_results[0].output[0].text
    assert _final_answer_deltas(events) == ["父 Agent 已继续。"]
    assert state.assistant_text == "父 Agent 已继续。"
    assert any(_event_contains(event, "SUBAGENT_COMPLETED") for event in events)


def test_parallel_delegate_external_events_are_resumed_as_one_complete_batch():
    """AgentScope 分开发出的并行 external 事件必须聚合后一次性回传。"""

    async def run_case():
        agent = _FakeParallelDelegateTaskParentAgent()
        manager = _FakeSubagentExternalManager()
        events = [
            event
            async for event in stream_leader_agent_reply(
                agent=agent,
                user_msg=object(),
                state=LeaderStreamState(),
                session_id="session-parallel-delegate",
                persist=False,
                agent_id="agent-1",
                agent_name="Agent",
                model_name="deepseek-chat",
                guardrails=[],
                message="并行委托探索",
                stream_scope="single",
                skill_runs=[],
                runtime_event_sources=[manager],
            )
        ]
        return events, agent, manager

    events, agent, manager = asyncio.run(run_case())

    assert manager.called_tool_ids == ["delegate-1", "delegate-2"]
    assert len(agent.resume_events) == 1
    assert [item.id for item in agent.resume_events[0].execution_results] == [
        "delegate-1",
        "delegate-2",
    ]
    assert _final_answer_deltas(events) == ["两个结果均已收到。"]


def test_delegate_task_external_executor_is_recovered_from_agent_toolkit():
    """HITL 恢复没有传 source 时，仍可从 live Agent 的 DelegateTask 找回 manager。"""

    class FakeToolkit:
        def __init__(self, manager) -> None:
            self.manager = manager

        async def get_tool(self, name: str):
            assert name == "DelegateTask"
            return type("DelegateTool", (), {"_manager": self.manager})()

    async def run_case():
        agent = _FakeDelegateTaskParentAgent()
        manager = _FakeSubagentExternalManager()
        agent.toolkit = FakeToolkit(manager)
        state = LeaderStreamState()
        events = [
            event
            async for event in stream_leader_agent_reply(
                agent=agent,
                user_msg=object(),
                state=state,
                session_id="session-delegate-toolkit",
                persist=False,
                agent_id="agent-1",
                agent_name="Agent",
                model_name="deepseek-chat",
                guardrails=[],
                message="委托探索",
                stream_scope="single",
                skill_runs=[],
            )
        ]
        return events, manager

    events, manager = asyncio.run(run_case())

    assert manager.called_tool_ids == ["delegate-1"]
    assert not any(_event_contains(event, "subagent_executor_missing") for event in events)


def test_subagent_completion_barrier_withholds_early_final_answer_and_resumes_parent():
    """父 Agent忘记消费 attached child 时，候选答案不能泄漏到前端。"""

    class BarrierSource:
        def __init__(self) -> None:
            self.checks = 0

        def drain_runtime_events(self):
            return []

        def completion_error(self):
            self.checks += 1
            return "child still running" if self.checks == 1 else ""

    class BarrierAgent:
        def __init__(self) -> None:
            self.calls = 0

        async def reply_stream(self, _event):
            self.calls += 1
            text = "不应展示" if self.calls == 1 else "已消费后回答"
            yield _FakeAgentScopeEvent({
                "type": "TOOL_CALL_START",
                "reply_id": f"reply-{self.calls}",
                "tool_call_id": f"final-{self.calls}",
                "tool_call_name": "FinalAnswerStart",
            })
            yield _FakeAgentScopeEvent({
                "type": "TEXT_BLOCK_DELTA",
                "reply_id": f"reply-{self.calls}",
                "delta": text,
            })
            yield _FakeAgentScopeEvent({"type": "REPLY_END", "reply_id": f"reply-{self.calls}"})

    async def run_case():
        agent = BarrierAgent()
        events = [
            event
            async for event in stream_leader_agent_reply(
                agent=agent,
                user_msg=object(),
                state=LeaderStreamState(),
                session_id="session-subagent-barrier",
                persist=False,
                agent_id="agent-1",
                agent_name="Agent",
                model_name="deepseek-chat",
                guardrails=AgentGuardrails(),
                message="delegate",
                stream_scope="single",
                skill_runs=[],
                runtime_event_sources=[BarrierSource()],
            )
        ]
        return agent, events

    agent, events = asyncio.run(run_case())

    assert agent.calls == 2
    assert _final_answer_deltas(events) == ["已消费后回答"]
    assert not any(_event_contains(event, "不应展示") for event in events)


def test_single_agent_without_final_answer_marker_does_not_fallback_to_final_answer():
    """没有任何工具调用时，模型自然输出应直接作为最终答案。"""

    async def run_case():
        return [
            event
            async for event in stream_leader_agent_reply(
                agent=_FakeNoFinalAnswerAgent(),
                user_msg=object(),
                state=LeaderStreamState(),
                session_id="session-no-final",
                persist=False,
                agent_id="agent-1",
                agent_name="Agent",
                model_name="deepseek-chat",
                guardrails=[],
                message="只输出过程",
                stream_scope="single",
                skill_runs=[],
            )
        ]

    events = asyncio.run(run_case())

    assert any(_event_is(event, "FINAL_ANSWER_DELTA") for event in events)
    assert not any(_event_is(event, "REASONING_DELTA") for event in events)
    assert not any(_event_contains(event, "missing_final_answer_marker_fallback") for event in events)


def test_single_agent_recovers_missing_final_answer_marker_with_extra_call():
    """完整 Reply 的尾部正文无需 FinalAnswerStart，也必须作为最终答案。"""

    async def run_case():
        return [
            event
            async for event in stream_leader_agent_reply(
                agent=_FakeRecoverableNoFinalAnswerAgent(),
                user_msg=object(),
                state=LeaderStreamState(),
                session_id="session-recover-final",
                persist=False,
                agent_id="agent-1",
                agent_name="Agent",
                model_name="deepseek-chat",
                guardrails=[],
                message="总结工具结果",
                stream_scope="single",
                skill_runs=[],
            )
        ]

    events = asyncio.run(run_case())

    final_events = [event for event in events if _event_is(event, "FINAL_ANSWER_DELTA")]
    assert any(_event_contains(event, "工具后的过程总结") for event in final_events)
    assert not any(_event_contains(event, "恢复后的最终答案") for event in final_events)
    assert not any(_event_contains(event, "recover_missing_final_answer_marker") for event in events)


def test_final_answer_recovery_passes_tool_calls_to_agentscope_2_0_5_batcher():
    """恢复轮必须按 AgentScope 2.0.5 签名向 batcher 传入 ToolCallBlock。"""

    expected_tool_calls = [SimpleNamespace(id="call-final", name="FinalAnswerStart")]

    class FakeLastMessage:
        def get_content_blocks(self, block_type):
            assert block_type == "tool_call"
            return expected_tool_calls

    class FakeAgent:
        def __init__(self):
            self.received_tool_calls = None

        def _get_last_msg(self):
            return FakeLastMessage()

        async def _batch_tool_calls(self, tool_calls):
            self.received_tool_calls = tool_calls
            return [SimpleNamespace(type="concurrent", tool_calls=tool_calls)]

        async def _execute_concurrent_tool_calls(self, tool_calls):
            yield _FakeAgentScopeEvent({
                "type": "TOOL_RESULT_END",
                "tool_call_id": tool_calls[0].id,
                "state": "success",
            })

    async def run_case():
        agent = FakeAgent()
        events = [event async for event in _execute_recovery_tool_calls(agent)]
        return agent, events

    agent, events = asyncio.run(run_case())

    assert agent.received_tool_calls is expected_tool_calls
    assert events[0].model_dump()["tool_call_id"] == "call-final"


def test_react_runtime_fails_when_agent_ends_without_final_answer(monkeypatch):
    """Agent 自然结束但没有最终答案时必须失败，避免最大迭代停止后前端无反馈。"""

    def fake_create_main_agent_from_spec(*args, **kwargs):
        return _FakeNoFinalAnswerAgent()

    monkeypatch.setattr(
        "src.server.routes.chat.react_runtime.create_main_agent_from_spec",
        fake_create_main_agent_from_spec,
    )

    async def run_case():
        ctx = ChatRunContext(
            session_id="session-missing-final",
            runtime_context=_test_runtime_context("session-missing-final"),
            agent_id="agent-1",
            agent_name="Agent",
            agent_config=None,
            persist_run=False,
            runtime_model_settings=SimpleNamespace(default_model="model", fallback_models=[]),
            runtime_model_config=RuntimeModelConfig(
                model_name="model",
                fallback_names=[],
                model_options=AgentModelOptions(max_react_iters=40),
            ),
            readiness=SimpleNamespace(ready=True),
            intent_route=IntentRoute(),
            effective_user_message="测试最大迭代停止",
            used_skills=[],
            used_skill_details=[],
            skill_runs=[],
            active_skill_run=None,
            model_name="model",
            system_prompt="prompt",
            guardrails=AgentGuardrails(),
            runtime_tool_scope=RuntimeToolScope(
                allow_mcp=False,
                allow_knowledge=False,
                allow_bash=False,
                allow_http=False,
            ),
            mcp_servers=[],
            knowledge_tools=[],
        )
        run_data = ChatMessage(
            session_id=ctx.session_id,
            message="测试最大迭代停止",
            agent_id=ctx.agent_id,
            agent_name=ctx.agent_name,
        )
        return [
            event
            async for event in execute_react_runtime(ctx=ctx, run_data=run_data)
        ]

    events = asyncio.run(run_case())

    assert any(_event_is(event, "FINAL_ANSWER_DELTA") for event in events)
    assert any(_event_contains(event, "RUN_COMPLETED") for event in events)
    assert not any(_event_contains(event, "RUN_FAILED") for event in events)
    assert not any(_event_is(event, "ERROR") for event in events)


def test_react_runtime_reports_agentscope_max_iters(monkeypatch):
    """收到 AgentScope EXCEED_MAX_ITERS 时必须用明确原因失败收口。"""

    def fake_create_main_agent_from_spec(*args, **kwargs):
        return _FakeExceedMaxItersAgent()

    monkeypatch.setattr(
        "src.server.routes.chat.react_runtime.create_main_agent_from_spec",
        fake_create_main_agent_from_spec,
    )

    async def run_case():
        ctx = ChatRunContext(
            session_id="session-max-iters",
            runtime_context=_test_runtime_context("session-max-iters"),
            agent_id="agent-1",
            agent_name="Agent",
            agent_config=None,
            persist_run=False,
            runtime_model_settings=SimpleNamespace(default_model="model", fallback_models=[]),
            runtime_model_config=RuntimeModelConfig(
                model_name="model",
                fallback_names=[],
                model_options=AgentModelOptions(max_react_iters=40),
            ),
            readiness=SimpleNamespace(ready=True),
            intent_route=IntentRoute(),
            effective_user_message="测试最大迭代停止",
            used_skills=[],
            used_skill_details=[],
            skill_runs=[],
            active_skill_run=None,
            model_name="model",
            system_prompt="prompt",
            guardrails=AgentGuardrails(),
            runtime_tool_scope=RuntimeToolScope(
                allow_mcp=False,
                allow_knowledge=False,
                allow_bash=False,
                allow_http=False,
            ),
            mcp_servers=[],
            knowledge_tools=[],
        )
        run_data = ChatMessage(
            session_id=ctx.session_id,
            message="测试最大迭代停止",
            agent_id=ctx.agent_id,
            agent_name=ctx.agent_name,
        )
        return [
            event
            async for event in execute_react_runtime(ctx=ctx, run_data=run_data)
        ]

    events = asyncio.run(run_case())

    assert any(_event_is(event, "REASONING_DELTA") for event in events)
    assert any(event.data.get("reason") == "max_react_iterations" for event in events)
    assert any(_event_contains(event, "RUN_FAILED") for event in events)
    assert any(_event_is(event, "ERROR") and _event_contains(event, "最大 ReAct 迭代次数") for event in events)
    assert not any(_event_contains(event, "RUN_COMPLETED") for event in events)


def test_react_runtime_recovers_final_answer_after_max_iters(monkeypatch):
    """达到最大迭代但已有过程文本时，仍先尝试最终答案恢复。"""

    def fake_create_main_agent_from_spec(*args, **kwargs):
        return _FakeRecoverableExceedMaxItersAgent()

    monkeypatch.setattr(
        "src.server.routes.chat.react_runtime.create_main_agent_from_spec",
        fake_create_main_agent_from_spec,
    )

    async def run_case():
        ctx = ChatRunContext(
            session_id="session-max-iters-recover",
            runtime_context=_test_runtime_context("session-max-iters-recover"),
            agent_id="agent-1",
            agent_name="Agent",
            agent_config=None,
            persist_run=False,
            runtime_model_settings=SimpleNamespace(default_model="model", fallback_models=[]),
            runtime_model_config=RuntimeModelConfig(
                model_name="model",
                fallback_names=[],
                model_options=AgentModelOptions(max_react_iters=40),
            ),
            readiness=SimpleNamespace(ready=True),
            intent_route=IntentRoute(),
            effective_user_message="测试最大迭代后收口",
            used_skills=[],
            used_skill_details=[],
            skill_runs=[],
            active_skill_run=None,
            model_name="model",
            system_prompt="prompt",
            guardrails=AgentGuardrails(),
            runtime_tool_scope=RuntimeToolScope(
                allow_mcp=False,
                allow_knowledge=False,
                allow_bash=False,
                allow_http=False,
            ),
            mcp_servers=[],
            knowledge_tools=[],
        )
        run_data = ChatMessage(
            session_id=ctx.session_id,
            message="测试最大迭代后收口",
            agent_id=ctx.agent_id,
            agent_name=ctx.agent_name,
        )
        return [
            event
            async for event in execute_react_runtime(ctx=ctx, run_data=run_data)
        ]

    events = asyncio.run(run_case())

    assert any(_event_is(event, "REASONING_DELTA") for event in events)
    assert any(_event_is(event, "FINAL_ANSWER_DELTA") and _event_contains(event, "恢复后的最终答案") for event in events)
    assert any(_event_contains(event, "RUN_COMPLETED") for event in events)
    assert not any(_event_contains(event, "RUN_FAILED") for event in events)
    assert not any(_event_is(event, "ERROR") for event in events)


def test_react_runtime_forces_final_answer_after_bare_max_iters(monkeypatch):
    """即使最大迭代事件前没有 reasoning 文本，也要强制追加最终答案收口调用。"""

    agent = _FakeRecoverableBareExceedMaxItersAgent()

    def fake_create_main_agent_from_spec(*args, **kwargs):
        return agent

    monkeypatch.setattr(
        "src.server.routes.chat.react_runtime.create_main_agent_from_spec",
        fake_create_main_agent_from_spec,
    )

    async def run_case():
        ctx = ChatRunContext(
            session_id="session-max-iters-bare-recover",
            runtime_context=_test_runtime_context("session-max-iters-bare-recover"),
            agent_id="agent-1",
            agent_name="Agent",
            agent_config=None,
            persist_run=False,
            runtime_model_settings=SimpleNamespace(default_model="model", fallback_models=[]),
            runtime_model_config=RuntimeModelConfig(
                model_name="model",
                fallback_names=[],
                model_options=AgentModelOptions(max_react_iters=40),
            ),
            readiness=SimpleNamespace(ready=True),
            intent_route=IntentRoute(),
            effective_user_message="测试空过程最大迭代后收口",
            used_skills=[],
            used_skill_details=[],
            skill_runs=[],
            active_skill_run=None,
            model_name="model",
            system_prompt="prompt",
            guardrails=AgentGuardrails(),
            runtime_tool_scope=RuntimeToolScope(
                allow_mcp=False,
                allow_knowledge=False,
                allow_bash=False,
                allow_http=False,
            ),
            mcp_servers=[],
            knowledge_tools=[],
        )
        run_data = ChatMessage(
            session_id=ctx.session_id,
            message="测试空过程最大迭代后收口",
            agent_id=ctx.agent_id,
            agent_name=ctx.agent_name,
        )
        return [
            event
            async for event in execute_react_runtime(ctx=ctx, run_data=run_data)
        ]

    events = asyncio.run(run_case())

    assert any(_event_is(event, "FINAL_ANSWER_DELTA") and _event_contains(event, "恢复后的最终答案") for event in events)
    assert any(_event_contains(event, "RUN_COMPLETED") for event in events)
    assert not any(_event_contains(event, "RUN_FAILED") for event in events)
    assert not any(_event_is(event, "ERROR") for event in events)
    assert "maximum iteration limit" in str(agent.recovery_message)


def test_single_agent_hitl_collects_every_parallel_permission_event():
    """并行审批必须排空 AgentScope 事件后再创建同一个交互。"""

    async def run_case():
        PENDING_INTERACTIONS.clear()
        PENDING_AGENTS.clear()
        events = [
            event
            async for event in stream_leader_agent_reply(
                agent=_FakeMultiToolHitlAgent(),
                user_msg=object(),
                state=LeaderStreamState(),
                session_id="session-multi-tool-hitl",
                persist=False,
                agent_id="agent-1",
                agent_name="Agent",
                model_name="deepseek-chat",
                guardrails=AgentGuardrails(),
                message="读取两个文件",
                stream_scope="single",
                skill_runs=[],
            )
        ]
        return events, list(PENDING_INTERACTIONS.values())

    events, interactions = asyncio.run(run_case())

    try:
        assert interactions
        assert [item["id"] for item in interactions[0].tool_calls] == ["call-read-1", "call-read-2"]
        assert [item["id"] for item in interactions[0].resume_tool_calls] == ["call-read-1", "call-read-2"]
        hitl_events = [event for event in events if _event_is(event, "HITL_REQUIRED")]
        assert len(hitl_events) == 1
        assert any(
            _event_is(event, "RUN_WAITING") and event.data.get("status") == "waiting_permission"
            for event in events
        )
        assert _event_contains(hitl_events[0], "call-read-1")
        assert _event_contains(hitl_events[0], "call-read-2")
    finally:
        PENDING_INTERACTIONS.clear()
        PENDING_AGENTS.clear()


def test_resume_event_covers_every_pending_tool_call_in_same_reply():
    """同一回复的待执行工具都必须获得确认结果，避免供应商拒绝工具消息序列。"""

    interaction = _create_pending_interaction(
        interaction_type="REQUIRE_USER_CONFIRM",
        agent=object(),
        session_id="session-resume-tool-filter",
        agent_id="agent-1",
        agent_name="Agent",
        model_name="deepseek-chat",
        reply_id="reply-1",
        tool_calls=[
            {
                "id": "call-waiting",
                "name": "Read",
                "input": '{"file_path":"a.py"}',
                "state": "asking",
            },
            {
                "id": "call-ui-only",
                "name": "Glob",
                "input": '{"pattern":"*.py"}',
                "state": "asking",
            },
        ],
        message="读取文件",
        assistant_text="",
        skill_runs=[],
        scope="single",
        persist=False,
    )

    try:
        resume_event = _build_resume_event(
            interaction,
            ChatResumeRequest.model_validate({
                "interactionId": interaction.id,
                "action": "approve",
            }),
        )

        assert [item.tool_call.id for item in resume_event.confirm_results] == [
            "call-waiting",
            "call-ui-only",
        ]
        assert [item["id"] for item in interaction.tool_calls] == ["call-waiting", "call-ui-only"]
    finally:
        PENDING_INTERACTIONS.clear()
        PENDING_AGENTS.clear()


def test_resume_event_can_remember_tool_approval_for_current_session():
    """用户选择记住确认时，恢复事件向 AgentScope 追加同类工具允许规则。"""

    interaction = _create_pending_interaction(
        interaction_type="REQUIRE_USER_CONFIRM",
        agent=object(),
        session_id="session-remember-approval",
        agent_id="agent-1",
        agent_name="Agent",
        model_name="deepseek-chat",
        reply_id="reply-1",
        tool_calls=[{
            "id": "call-bash",
            "name": "Bash",
            "input": '{"command":"uv run pytest"}',
            "state": "asking",
        }],
        message="运行测试",
        assistant_text="",
        skill_runs=[],
        scope="single",
        persist=False,
    )

    try:
        resume_event = _build_resume_event(
            interaction,
            ChatResumeRequest.model_validate({
                "interactionId": interaction.id,
                "action": "approve",
                "rememberApproval": True,
            }),
        )

        rules = resume_event.confirm_results[0].rules
        assert rules is not None
        assert [(rule.tool_name, rule.rule_content, rule.behavior.value, rule.source) for rule in rules] == [
            ("Bash", None, "allow", "chat_session_approval"),
        ]
    finally:
        PENDING_INTERACTIONS.clear()
        PENDING_AGENTS.clear()


def test_resume_text_before_final_answer_marker_stays_out_of_final_body():
    """审批恢复后的过程文本不能闪进正文，只有 FinalAnswerStart 后的文本进入正文。"""

    async def run_case():
        PENDING_INTERACTIONS.clear()
        PENDING_AGENTS.clear()
        interaction = _create_pending_interaction(
            interaction_type="REQUIRE_USER_CONFIRM",
            agent=_FakeResumeFinalAnswerAgent(),
            session_id="session-resume-final",
            agent_id="agent-1",
            agent_name="Agent",
            model_name="deepseek-chat",
            reply_id="reply-1",
            tool_calls=[{
                "id": "call-read",
                "name": "Read",
                "input": '{"file_path":"docs/chat-sse-event-contract.md"}',
                "state": "asking",
            }],
            message="读取文件后回答",
            assistant_text="",
            skill_runs=[],
            scope="single",
            persist=False,
        )
        stream = resume_interaction_events(
            ChatResumeRequest.model_validate({
                "interactionId": interaction.id,
                "action": "approve",
            })
        )
        return [chunk async for chunk in stream]

    events = asyncio.run(run_case())

    try:
        final_events = [
            event for event in events
            if _event_is(event, "FINAL_ANSWER_DELTA")
        ]
        assert not any(_event_contains(event, "过程文本") for event in final_events)
        assert any(_event_contains(event, "最终文本") for event in final_events)
    finally:
        PENDING_INTERACTIONS.clear()
        PENDING_AGENTS.clear()


def test_permission_resume_promotes_completed_tail_text_without_final_answer_marker():
    """批准 Write 后，Read 之后的完整尾部正文必须正常收口为最终答案。"""

    class FakePermissionResumeAgent:
        async def reply_stream(self, _resume_event):
            for payload in (
                {
                    "type": "TOOL_RESULT_START",
                    "reply_id": "reply-permission",
                    "tool_call_id": "call-write",
                    "tool_call_name": "Write",
                },
                {
                    "type": "TOOL_RESULT_END",
                    "reply_id": "reply-permission",
                    "tool_call_id": "call-write",
                    "tool_call_name": "Write",
                    "state": "success",
                },
                {
                    "type": "TOOL_CALL_START",
                    "reply_id": "reply-permission",
                    "tool_call_id": "call-read",
                    "tool_call_name": "Read",
                },
                {
                    "type": "TOOL_CALL_END",
                    "reply_id": "reply-permission",
                    "tool_call_id": "call-read",
                },
                {
                    "type": "TOOL_RESULT_END",
                    "reply_id": "reply-permission",
                    "tool_call_id": "call-read",
                    "tool_call_name": "Read",
                    "state": "success",
                },
                {
                    "type": "TEXT_BLOCK_DELTA",
                    "reply_id": "reply-permission",
                    "delta": "agentscope-2.0.5 permission check",
                },
                {
                    "type": "REPLY_END",
                    "reply_id": "reply-permission",
                    "finished_reason": "completed",
                },
            ):
                yield _FakeAgentScopeEvent(payload)

    async def run_case():
        PENDING_INTERACTIONS.clear()
        PENDING_AGENTS.clear()
        interaction = _create_pending_interaction(
            interaction_type="REQUIRE_USER_CONFIRM",
            agent=FakePermissionResumeAgent(),
            session_id="session-permission-final-answer",
            agent_id="agent-1",
            agent_name="Agent",
            model_name="deepseek-chat",
            reply_id="reply-permission",
            tool_calls=[{
                "id": "call-write",
                "name": "Write",
                "input": '{"file_path":"artifacts/permission.txt","content":"permission check"}',
                "state": "asking",
            }],
            message="写入并读取文件后原样回答",
            assistant_text="",
            skill_runs=[],
            scope="single_agent",
            persist=False,
        )
        stream = resume_interaction_events(
            ChatResumeRequest.model_validate({
                "interactionId": interaction.id,
                "action": "approve",
            })
        )
        return [chunk async for chunk in stream]

    events = asyncio.run(run_case())

    try:
        assert _final_answer_deltas(events) == ["agentscope-2.0.5 permission check"]
        assert any(_event_contains(event, "RUN_COMPLETED") for event in events)
        assert not any(_event_contains(event, "RUN_FAILED") for event in events)
        assert not any(_event_contains(event, "final_answer_recovery_failed") for event in events)
    finally:
        PENDING_INTERACTIONS.clear()
        PENDING_AGENTS.clear()


def test_resume_without_final_answer_marker_does_not_fallback_to_final_answer():
    """resume 阶段没有 FinalAnswerStart 时，也不能把过程文本兜底成最终答案。"""

    async def run_case():
        PENDING_INTERACTIONS.clear()
        PENDING_AGENTS.clear()
        interaction = _create_pending_interaction(
            interaction_type="REQUIRE_USER_CONFIRM",
            agent=_FakeNoFinalAnswerAgent(),
            session_id="session-resume-no-final",
            agent_id="agent-1",
            agent_name="Agent",
            model_name="deepseek-chat",
            reply_id="reply-1",
            tool_calls=[{
                "id": "call-read",
                "name": "Read",
                "input": '{"file_path":"README.md"}',
                "state": "asking",
            }],
            message="读取文件后回答",
            assistant_text="",
            skill_runs=[],
            scope="single",
            persist=False,
        )
        stream = resume_interaction_events(
            ChatResumeRequest.model_validate({
                "interactionId": interaction.id,
                "action": "approve",
            })
        )
        return [chunk async for chunk in stream]

    events = asyncio.run(run_case())

    try:
        assert any(_event_is(event, "FINAL_ANSWER_DELTA") for event in events)
        assert not any(_event_contains(event, "missing_final_answer_marker_fallback") for event in events)
        assert any(_event_contains(event, "RUN_COMPLETED") for event in events)
        assert not any(_event_contains(event, "RUN_FAILED") for event in events)
        assert not any(_event_is(event, "ERROR") for event in events)
    finally:
        PENDING_INTERACTIONS.clear()
        PENDING_AGENTS.clear()


def test_resume_recovers_missing_final_answer_marker_with_extra_call():
    """resume 阶段漏调 FinalAnswerStart 时，也通过受限恢复轮重新生成最终答案。"""

    async def run_case():
        PENDING_INTERACTIONS.clear()
        PENDING_AGENTS.clear()
        interaction = _create_pending_interaction(
            interaction_type="REQUIRE_USER_CONFIRM",
            agent=_FakeRecoverableNoFinalAnswerAgent(),
            session_id="session-resume-recover-final",
            agent_id="agent-1",
            agent_name="Agent",
            model_name="deepseek-chat",
            reply_id="reply-1",
            tool_calls=[{
                "id": "call-read",
                "name": "Read",
                "input": '{"file_path":"README.md"}',
                "state": "asking",
            }],
            message="读取文件后回答",
            assistant_text="",
            skill_runs=[],
            scope="single",
            persist=False,
        )
        stream = resume_interaction_events(
            ChatResumeRequest.model_validate({
                "interactionId": interaction.id,
                "action": "approve",
            })
        )
        return [chunk async for chunk in stream]

    events = asyncio.run(run_case())

    try:
        final_events = [
            event for event in events
            if _event_is(event, "FINAL_ANSWER_DELTA")
        ]
        assert any(_event_contains(event, "工具后的过程总结") for event in final_events)
        assert not any(_event_contains(event, "恢复后的最终答案") for event in final_events)
        assert any(_event_contains(event, "RUN_COMPLETED") for event in events)
        assert not any(_event_contains(event, "RUN_FAILED") for event in events)
    finally:
        PENDING_INTERACTIONS.clear()
        PENDING_AGENTS.clear()


def test_plan_review_waits_for_tool_result_end_before_stopping():
    """PLAN_REVIEW_REQUESTED 必须等工具结果闭合后再停流。"""

    async def run_case():
        state = LeaderStreamState()
        events = [
            event
            async for event in stream_leader_agent_reply(
                agent=_FakePlanReviewThenContinuesAgent(),
                user_msg=object(),
                state=state,
                session_id="session-1",
                persist=False,
                agent_id="agent-1",
                agent_name="Agent",
                model_name="deepseek-chat",
                guardrails=AgentGuardrails(),
                message="制定计划",
                stream_scope="single",
                skill_runs=[],
                plan_mode_manager=_FakeNativePlanReviewManager(),
            )
        ]
        return state, events

    state, events = asyncio.run(run_case())

    tool_result_end_index = next(index for index, event in enumerate(events) if _event_is(event, "TOOL_RESULT_END"))
    final_answer_index = next(index for index, event in enumerate(events) if _event_is(event, "FINAL_ANSWER_DELTA"))
    assert tool_result_end_index < final_answer_index
    final_deltas = _final_answer_deltas(events)
    assert final_deltas
    assert "".join(final_deltas) == "计划 Revision 0 已生成，等待你的确认。"
    assert state.assistant_tool_calls[0].id == "call-plan-review"
    assert state.assistant_tool_calls[0].status == "completed"
    assert state.stopped is True
    assert state.stop_reason == "plan_review_requested"
    assert not any(_event_contains(event, "call-write-after-plan-review") for event in events)


def test_context_compression_schema_allows_project_discovery_summaries():
    context_config = _build_context_config()

    schema = context_config.summary_schema

    assert schema["properties"]["important_discoveries"]["maxLength"] >= 1000
    assert schema["properties"]["next_steps"]["maxLength"] >= 1000
    # 只声明 summary_template 会渲染的五段；模型多返回 title / summary 时靠 additionalProperties 容忍。
    assert set(schema["properties"]) == {
        "task_overview",
        "current_state",
        "important_discoveries",
        "next_steps",
        "context_to_preserve",
    }
    assert schema["additionalProperties"] is True
    assert "required" not in schema
    assert "under the schema maxLength" in context_config.compression_prompt
    # 触发点早于 AgentScope 默认 0.8，保留比例高于默认 0.1。
    assert context_config.trigger_ratio == 0.75
    assert context_config.reserve_ratio == 0.2
    assert context_config.tool_result_limit == 4000


def test_resume_text_event_scopes_are_separated():
    assert _is_final_answer_text_scope("single") is True
    assert _is_final_answer_text_scope("single_agent") is True
    assert _is_final_answer_text_scope("leader") is False
    assert _should_wrap_resume_text_as_final("TEXT_BLOCK_DELTA", "single", True) is True
    assert _should_wrap_resume_text_as_final("TEXT_BLOCK_DELTA", "single", False) is False
    assert _should_wrap_resume_text_as_final("TEXT_BLOCK_DELTA", "leader", True) is False


def test_model_cache_from_event_maps_provider_cache_usage():
    cache = _model_cache_from_event(
        {
            "id": "evt-2",
            "input_tokens": 100,
            "output_tokens": 20,
            "cache_creation_input_tokens": 32,
            "cache_input_tokens": 50,
            "created_at": "2026-06-18T00:00:00",
        },
        "deepseek-chat",
    )

    assert cache is not None
    assert cache.model_name == "deepseek-chat"
    assert cache.cache_creation_input_tokens == 32
    assert cache.cache_input_tokens == 50
    assert cache.cache_hit_rate == 50 / 150


def test_ask_user_is_external_tool_for_agentscope_resume():
    tool = AskUser()

    assert tool.name == "AskUser"
    assert tool.is_external_tool is True
    assert tool.input_schema["required"] == ["title", "questions"]
    question_schema = tool.input_schema["properties"]["questions"]["items"]
    assert question_schema["required"] == ["id", "type", "label", "required"]
    assert "optional supporting details" in question_schema["properties"]["required"]["description"]


def test_intent_route_only_selects_react_architecture_and_executor():
    route = route_chat_message(execution_mode="agent")

    assert route.mode == "react"
    assert route.runtime.kind == "react"
    assert route.executor.kind == "single_agent"
    assert not hasattr(route, "selected_subagent_ids")
    assert not hasattr(route, "tool_scope")


def test_intent_route_trace_payload_uses_api_aliases():
    route = route_chat_message(execution_mode="agent")

    payload = route_trace_payload(route)

    assert payload["mode"] == "react"
    assert payload["runtime"]["kind"] == "react"
    assert payload["executor"]["kind"] == "single_agent"
    assert "requireClarification" not in payload
    assert "selectedSkillIds" not in payload
    assert "selectedSubagentIds" not in payload


def test_intent_route_team_executor_is_mechanical():
    route = route_chat_message(execution_mode="team")

    assert route.mode == "react"
    assert route.executor.kind == "team"
    assert route.executor.payload["executionMode"] == "team"


def test_chat_message_rejects_persistent_team_payload():
    try:
        ChatMessage(
            message="测试",
            mode="team",
            agent_id="leader-agent",
            team_id="legacy-team",
            team_members=[],
        )
    except ValidationError as exc:
        rejected_fields = {error["loc"][0] for error in exc.errors()}
    else:
        raise AssertionError("旧 Team 模板字段必须被请求模型拒绝")

    assert {"team_id", "team_members"}.issubset(rejected_fields)


def test_team_rerun_reuses_mode_and_leader_without_team_template(monkeypatch):
    monkeypatch.setattr(
        "src.server.routes.chat.handlers.runs.get_latest_user_chat_message",
        lambda session_id, fallback: {"content": "重新执行", "images": []},
    )
    monkeypatch.setattr(
        "src.server.routes.chat.handlers.runs.get_registered_agent",
        lambda agent_id: SimpleNamespace(name="Leader"),
    )

    request = _build_rerun_chat_message(SimpleNamespace(
        session_id="session-team",
        task_text="原任务",
        mode="team",
        agent_id="leader-agent",
        agent_name="Leader",
    ))

    assert request.mode == "team"
    assert request.agent_id == "leader-agent"
    assert "team_id" not in ChatMessage.model_fields
    assert "team_members" not in ChatMessage.model_fields


def test_intent_route_tool_scope_stays_minimal_for_conversation():
    decision = resolve_runtime_tool_scope(
        message="你好，解释一下这个概念。",
        agent_config=SimpleNamespace(
            mcp_tools=["mcp-1"],
            knowledge_tools=["kb-1"],
        ),
        has_selected_skills=False,
        has_team=False,
        has_images=False,
    )

    assert decision.scope.allow_mcp is False
    assert decision.scope.allow_knowledge is False
    assert decision.scope.allow_bash is True
    assert decision.scope.allow_http is False
    assert decision.scope.allow_web is False


def test_intent_route_tool_scope_enables_execution_and_research_tools():
    decision = resolve_runtime_tool_scope(
        message="请运行 pytest 并搜索知识库资料分析失败原因。",
        agent_config=SimpleNamespace(
            mcp_tools=["mcp-1"],
            knowledge_tools=["kb-1"],
        ),
        has_selected_skills=False,
        has_team=False,
        has_images=False,
    )

    assert decision.scope.allow_bash is True
    assert decision.scope.allow_http is False
    assert decision.scope.allow_web is True
    assert decision.scope.allow_knowledge is True


def test_runtime_tool_scope_separates_api_requests_from_web_fetch():
    decision = resolve_runtime_tool_scope(
        message="请调用 REST API POST /reports 创建报告。",
        agent_config=SimpleNamespace(mcp_tools=[], knowledge_tools=[]),
        has_selected_skills=False,
        has_team=False,
        has_images=False,
    )

    assert decision.scope.allow_http is True
    assert decision.scope.allow_web is False


class _FakeTextDeltaEvent:
    def __init__(self, delta: str):
        self.delta = delta

    def model_dump(self, mode: str = "json"):
        return {"type": "TEXT_BLOCK_DELTA", "delta": self.delta}


class _FakeRouterAgent:
    def __init__(self, text: str):
        self.text = text

    async def reply_stream(self, message):
        yield _FakeTextDeltaEvent(self.text)


def test_model_intent_route_ignores_legacy_plan_subagent_and_tool_scope(monkeypatch):
    parent = SimpleNamespace(
        id="parent-agent",
        name="Parent",
        description="",
        plan_config=SimpleNamespace(enabled=True),
        mcp_tools=[],
        knowledge_tools=["kb-1"],
    )
    children = {
        "code-agent": SimpleNamespace(
            id="code-agent",
            name="Code Agent",
            description="负责代码实现、修复和测试。",
            system_prompt="",
        ),
    }
    router_json = """
    {
      "mode": "agent_execution",
      "intent": "code_change",
      "confidence": 0.91,
      "reason": "需要修改代码并运行验证。",
      "shouldCreatePlan": true,
      "requireClarification": false,
      "selectedSubagentIds": ["code-agent"],
      "selectedSkillIds": [],
      "selectedTeamMemberIds": [],
      "toolScope": {
        "allowMcp": true,
        "allowKnowledge": true,
        "allowBash": true,
        "allowHttp": false,
        "reason": "代码修改需要测试，且需要知识库上下文。"
      }
    }
    """

    monkeypatch.setattr(
        "src.server.routes.agents.get_registered_agent",
        lambda agent_id: children.get(agent_id),
    )
    monkeypatch.setattr(
        "src.server.routes.chat.intent_router.get_or_create_agent",
        lambda **kwargs: _FakeRouterAgent(router_json),
    )
    # 当前目录只有 react，默认不调模型；这里强制走模型路径以验证 sanitizer。
    monkeypatch.setattr("src.server.routes.chat.intent_router.model_routing_required", lambda: True)

    route = asyncio.run(route_chat_message_with_model(
        message="请让代码 Agent 修复项目中的测试问题，并运行 pytest 验证。",
        agent_config=parent,
        execution_mode="agent",
        images=[],
        model_name="test-model",
    ))

    assert route.mode == "react"
    assert route.runtime.kind == "react"
    assert route.executor.kind == "single_agent"
    assert not hasattr(route, "should_create_plan")
    assert not hasattr(route, "selected_subagent_ids")
    assert not hasattr(route, "tool_scope")
    model_decision = next(decision for decision in route.decisions if decision.name == "architecture_route")
    assert model_decision.payload["intent"] == "code_change"
    source = next(decision for decision in route.decisions if decision.name == "architecture_router_source")
    assert source.payload["modelCalled"] is True


def test_intent_route_skips_model_when_only_one_architecture(monkeypatch):
    """INTENT_CATALOG 只有 react 时不得调用模型，INTENT_ROUTE trace 记录 modelCalled=False。"""

    def boom(**kwargs):
        raise AssertionError("single-architecture catalog must not call the model")

    monkeypatch.setattr("src.server.routes.chat.intent_router.get_or_create_agent", boom)

    route = asyncio.run(route_chat_message_with_model(
        message="帮我整理一下这周的会议纪要",
        agent_config=None,
        execution_mode="agent",
        images=[],
        model_name="test-model",
    ))

    assert route.mode == "react"
    source = next(decision for decision in route.decisions if decision.name == "architecture_router_source")
    assert source.payload["modelCalled"] is False


def test_readiness_skips_model_when_rule_finds_no_gap(monkeypatch):
    """规则没发现缺口时 readiness 不调模型；发现模糊执行型请求时才调，并记录 modelCalled。"""

    from src.server.routes.chat import readiness_gate

    calls = {"count": 0}

    async def fake_agent_run(**kwargs):
        calls["count"] += 1
        return readiness_gate.ModelReadinessPayload(ready=True, confidence=0.8, reason="模型判断可以开始")

    monkeypatch.setattr(readiness_gate, "_run_readiness_agent", fake_agent_run)

    clear = asyncio.run(readiness_gate.check_message_readiness_with_model(
        message="请总结 docs/architecture-overview.md 的要点",
        agent_config=None,
        skill_ids=[],
        execution_mode="agent",
        images=[],
        model_name="test-model",
    ))
    assert clear.ready is True
    assert clear.model_called is False
    assert calls["count"] == 0

    ambiguous = asyncio.run(readiness_gate.check_message_readiness_with_model(
        message="帮我修改一下",
        agent_config=None,
        skill_ids=[],
        execution_mode="agent",
        images=[],
        model_name="test-model",
    ))
    assert ambiguous.ready is True
    assert ambiguous.model_called is True
    assert ambiguous.model_name == "test-model"
    assert ambiguous.reason == "模型判断可以开始"
    assert calls["count"] == 1


def test_model_intent_route_falls_back_to_rules_on_invalid_json(monkeypatch):
    monkeypatch.setattr(
        "src.server.routes.chat.intent_router.get_or_create_agent",
        lambda **kwargs: _FakeRouterAgent("not json"),
    )
    monkeypatch.setattr("src.server.routes.chat.intent_router.model_routing_required", lambda: True)

    route = asyncio.run(route_chat_message_with_model(
        message="请修复一下。",
        agent_config=SimpleNamespace(
            id="agent",
            plan_config=SimpleNamespace(enabled=True),
            mcp_tools=[],
            knowledge_tools=[],
        ),
        execution_mode="agent",
        images=[],
        model_name="test-model",
    ))

    assert route.mode == "react"
    assert route.runtime.kind == "react"
    assert route.executor.kind == "single_agent"


def test_runtime_tool_scope_cache_key_is_stable():
    scope = RuntimeToolScope(
        allow_mcp=False,
        allow_knowledge=True,
        allow_bash=False,
        allow_http=True,
    )

    assert scope.cache_key() == "mcp-0:knowledge-1:bash-0:http-1:web-1"


def test_runtime_builder_ignores_legacy_request_and_agent_models():
    config = build_runtime_model_config(
        run_data=SimpleNamespace(model="request-model"),
        agent_config=SimpleNamespace(
            model_config_data=SimpleNamespace(
                primary="agent-model",
                fallback=["fallback-a"],
                temperature=0.2,
                top_p=0.9,
                max_tokens=1024,
                max_react_iters=12,
            ),
        ),
        runtime_model_settings=SimpleNamespace(
            default_model="runtime-model",
            fallback_models=["runtime-fallback"],
        ),
    )

    assert config.model_name == "runtime-model"
    assert config.fallback_names == ["runtime-fallback"]
    assert config.model_options.temperature is None
    assert config.model_options.max_react_iters == 12


def test_runtime_builder_default_agent_follows_runtime_model_settings():
    config = build_runtime_model_config(
        run_data=SimpleNamespace(model=""),
        agent_config=SimpleNamespace(
            id="default",
            model_config_data=SimpleNamespace(
                primary="old-provider-model",
                fallback=["old-fallback"],
                temperature=0.2,
                top_p=0.9,
                max_tokens=1024,
                max_react_iters=12,
            ),
        ),
        runtime_model_settings=SimpleNamespace(
            default_model="new-provider-model",
            fallback_models=["new-fallback"],
        ),
    )

    assert config.model_name == "new-provider-model"
    assert config.fallback_names == ["new-fallback"]
    assert config.model_options.max_react_iters == 12


def test_runtime_builder_custom_agent_uses_run_model():
    config = build_runtime_model_config(
        run_data=SimpleNamespace(model=""),
        agent_config=SimpleNamespace(
            id="custom-agent",
            model_config_data=SimpleNamespace(
                primary="custom-model",
                fallback=["custom-fallback"],
                temperature=0.7,
                top_p=0.8,
                max_tokens=4096,
                max_react_iters=40,
            ),
        ),
        runtime_model_settings=SimpleNamespace(
            default_model="runtime-model",
            fallback_models=["runtime-fallback"],
        ),
    )

    assert config.model_name == "runtime-model"
    assert config.fallback_names == ["runtime-fallback"]


def test_runtime_builder_legacy_pinned_agent_uses_run_model():
    config = build_runtime_model_config(
        run_data=SimpleNamespace(model=""),
        agent_config=SimpleNamespace(
            id="default",
            model_config_data=SimpleNamespace(
                primary="pinned-model",
                fallback=["pinned-fallback"],
                temperature=0.7,
                top_p=0.8,
                max_tokens=4096,
                max_react_iters=40,
            ),
        ),
        runtime_model_settings=SimpleNamespace(
            default_model="runtime-model",
            fallback_models=["runtime-fallback"],
        ),
    )

    assert config.model_name == "runtime-model"
    assert config.fallback_names == ["runtime-fallback"]


def test_runtime_builder_cache_key_includes_tool_scope():
    key = build_runtime_agent_cache_key(
        agent_id="agent",
        session_id="session",
        system_prompt="prompt-a",
        model_name="model",
        deep_thinking=False,
        fallback_names=["fallback"],
        used_skills=[],
        mcp_servers=[],
        knowledge_tools=[],
        runtime_tool_scope=RuntimeToolScope(allow_mcp=False, allow_knowledge=False, allow_bash=True, allow_http=False),
        guardrail_cache_key="guardrails",
    )

    assert "tools-mcp-0:knowledge-0:bash-1:http-0" in key
    assert "plan-" not in key
    # system prompt 正文不进缓存键：同一 session 多轮复用同一实例，AgentScope 上下文即会话记忆。
    assert "prompt-" not in key


def test_chat_run_context_includes_memory_prompt(monkeypatch):
    async def fake_readiness(**kwargs):
        return SimpleNamespace(ready=True)

    async def fake_route(**kwargs):
        return IntentRoute(
            runtime={"kind": "react"},
            executor={"kind": "single_agent"},
            main_agent_instruction="",
            trace_payload={},
        )

    monkeypatch.setattr(
        "src.server.routes.chat.run_context.check_message_readiness_with_model",
        fake_readiness,
    )
    monkeypatch.setattr(
        "src.server.routes.chat.run_context.route_chat_message_with_model",
        fake_route,
    )
    monkeypatch.setattr(
        "src.server.routes.chat.run_context.get_effective_model_settings",
        lambda: SimpleNamespace(default_model="model", fallback_models=[]),
    )
    monkeypatch.setattr(
        "src.server.routes.chat.runtime_governance.build_memory_injection_text",
        lambda context: "# Memory\n记忆注入片段",
    )
    monkeypatch.setattr(
        "src.server.routes.chat.run_context.resolve_project_resource_access",
        lambda context: SimpleNamespace(
            require_agent=lambda agent_id: None,
            filter_ids=lambda resource_type, resource_ids: resource_ids,
            skill_ids=frozenset(),
            mcp_server_ids=frozenset(),
            http_tool_ids=frozenset(),
        ),
    )

    ctx = asyncio.run(
        build_chat_run_context(
            ChatMessage(
                session_id="session-1",
                message="继续推进",
                agent_id="default",
                agent_name="Agent",
                system_prompt="base prompt",
            ),
            _test_runtime_context(),
        ),
    )

    assert "# Memory\n记忆注入片段" in ctx.system_prompt


def test_chat_run_context_keeps_history_out_of_system_prompt(monkeypatch):
    """会话历史进 AgentScope 上下文快照而不是 system prompt；附件说明随用户消息走；prompt 不含时钟。"""

    async def fake_readiness(**kwargs):
        return SimpleNamespace(ready=True)

    async def fake_route(**kwargs):
        return IntentRoute(
            runtime={"kind": "react"},
            executor={"kind": "single_agent"},
            main_agent_instruction="",
            trace_payload={},
        )

    monkeypatch.setattr("src.server.routes.chat.run_context.check_message_readiness_with_model", fake_readiness)
    monkeypatch.setattr("src.server.routes.chat.run_context.route_chat_message_with_model", fake_route)
    monkeypatch.setattr(
        "src.server.routes.chat.run_context.get_effective_model_settings",
        lambda: SimpleNamespace(default_model="model", fallback_models=[]),
    )
    monkeypatch.setattr("src.server.routes.chat.runtime_governance.build_memory_injection_text", lambda context: "")
    monkeypatch.setattr(
        "src.server.routes.chat.run_context.resolve_project_resource_access",
        lambda runtime_context: SimpleNamespace(
            require_agent=lambda agent_id: None,
            filter_ids=lambda resource_type, resource_ids: resource_ids,
            skill_ids=frozenset(),
            mcp_server_ids=frozenset(),
            http_tool_ids=frozenset(),
        ),
    )
    monkeypatch.setattr(
        "src.server.routes.chat.run_context.snapshot_session_history",
        lambda session_id, limit=40: [
            SessionHistoryMessage(role="user", content="上一轮：请整理会议纪要"),
            SessionHistoryMessage(role="assistant", content="上一轮回答：已整理"),
        ],
    )

    ctx = asyncio.run(
        build_chat_run_context(
            ChatMessage(
                session_id="session-1",
                message="继续推进",
                agent_id="default",
                agent_name="Agent",
                system_prompt="base prompt",
                files=[
                    ChatFilePayload(name="报表.xlsx", path="uploads/session-1/报表.xlsx", size=12, media_type="application/octet-stream"),
                ],
            ),
            _test_runtime_context(),
        ),
    )

    assert "上一轮：请整理会议纪要" not in ctx.system_prompt
    assert "## Conversation History" not in ctx.system_prompt
    assert "Current date/time" not in ctx.system_prompt
    assert "<current-time>" in ctx.system_prompt
    assert "报表.xlsx" not in ctx.system_prompt
    assert "<attached_files>" in ctx.user_message_note and "报表.xlsx" in ctx.user_message_note
    assert [item.role for item in ctx.session_history] == ["user", "assistant"]


def test_runtime_builder_cache_key_ignores_system_prompt():
    base_kwargs = dict(
        agent_id="agent",
        session_id="session",
        model_name="model",
        deep_thinking=False,
        fallback_names=["fallback"],
        used_skills=[],
        mcp_servers=[],
        knowledge_tools=[],
        runtime_tool_scope=RuntimeToolScope(allow_mcp=False, allow_knowledge=False, allow_bash=True, allow_http=False),
        guardrail_cache_key="guardrails",
    )
    key_a = build_runtime_agent_cache_key(system_prompt="prompt-a", **base_kwargs)
    key_b = build_runtime_agent_cache_key(system_prompt="prompt-b", **base_kwargs)

    # 记忆摘要 / Skill Run 提示每轮都会改动 prompt，若进 key 会导致每轮新建实例并丢失会话记忆。
    assert key_a == key_b


def test_main_agent_runtime_spec_wraps_factory_inputs():
    tool_scope = RuntimeToolScope(allow_mcp=False, allow_knowledge=False, allow_bash=True, allow_http=False)
    spec = build_main_agent_runtime_spec(
        agent_id="agent",
        agent_name="Agent",
        session_id="session",
        system_prompt="prompt",
        model_config=RuntimeModelConfig(
            model_name="model",
            fallback_names=["fallback"],
            model_options=AgentModelOptions(max_react_iters=9),
        ),
        deep_thinking=True,
        used_skills=[],
        guardrails=AgentGuardrails(),
        mcp_servers=[],
        knowledge_tools=[],
        runtime_tool_scope=tool_scope,
        guardrail_cache_key="guardrails",
    )

    assert spec.name == "Agent"
    assert spec.parent_agent_id == "agent"
    assert spec.session_id == "session"
    assert spec.tool_scope is tool_scope
    assert "thinking-True" in spec.runtime_agent_id
    assert "prompt-" not in spec.runtime_agent_id
    assert spec.session_context_key == "agent::session"


def test_create_main_agent_from_spec_delegates_to_factory(monkeypatch):
    calls = {}

    def fake_get_or_create_agent(**kwargs):
        calls.update(kwargs)
        return "agent-instance"

    monkeypatch.setattr(
        "src.server.routes.chat.runtime_builder.get_or_create_agent",
        fake_get_or_create_agent,
    )
    tool_scope = RuntimeToolScope(allow_mcp=False, allow_knowledge=False, allow_bash=True, allow_http=False)
    spec = build_main_agent_runtime_spec(
        agent_id="agent",
        agent_name="Agent",
        session_id="session",
        system_prompt="prompt",
        model_config=RuntimeModelConfig(
            model_name="model",
            fallback_names=["fallback"],
            model_options=AgentModelOptions(max_react_iters=9),
        ),
        deep_thinking=False,
        used_skills=[],
        guardrails=AgentGuardrails(),
        mcp_servers=[],
        knowledge_tools=[],
        runtime_tool_scope=tool_scope,
        guardrail_cache_key="guardrails",
    )

    result = create_main_agent_from_spec(
        spec,
        on_fallback_event=lambda event: None,
    )

    assert result == "agent-instance"
    assert calls["agent_id"] == spec.runtime_agent_id
    assert calls["name"] == "Agent"
    assert calls["model_name"] == "model"
    assert calls["session_id"] == "session"
    assert calls["tool_scope"] is tool_scope
    assert calls["include_default_tools"] is True
    assert callable(calls["on_fallback_event"])


def test_unified_permission_profile_maps_legacy_bypass_to_trusted_tools():
    guardrails = AgentGuardrails(
        tool_permission_mode="bypass",
        tool_permission_profile="",
    )

    profile = build_permission_profile(guardrails)

    assert profile.tool_policy == "trusted_tools"
    assert profile.decide(ResourceType.TOOL, PermissionAction.READ) == PermissionDecision.ALLOW


def test_default_workspace_dir_is_project_root_not_user_home():
    """默认 LocalWorkspace 应收窄到项目根，避免 Glob/Grep 扫用户主目录。"""

    assert WORKSPACE_DIR != str(Path.home())
    assert Path(WORKSPACE_DIR).is_dir()


def test_legacy_root_filesystem_scope_maps_to_project_workspace():
    """历史默认 filesystem_scope='/' 不应被映射到用户主目录。"""

    guardrails = AgentGuardrails(filesystem_scope="/")

    assert _safe_workspace_dir(guardrails) == WORKSPACE_DIR


def test_storage_normalizes_legacy_root_filesystem_scope():
    """历史库中保存的 '/' 读出时也展示为项目根目录。"""

    guardrails = _normalize_guardrails({"filesystem_scope": "/"})

    assert guardrails["filesystem_scope"] == WORKSPACE_DIR


def test_unified_permission_profile_denies_write_in_read_only():
    guardrails = AgentGuardrails(tool_permission_profile="read_only")

    profile = build_permission_profile(guardrails)

    assert profile.is_read_only is True
    assert profile.can_use_network is False
    assert profile.decide(ResourceType.TOOL, PermissionAction.WRITE) == PermissionDecision.DENY


def test_guardrails_human_approval_uses_agentscope_default_permission_mode():
    from agentscope.permission import PermissionMode

    guardrails = AgentGuardrails(
        human_approval=True,
        tool_permission_profile="trusted_tools",
    )

    assert _permission_mode_for_guardrails(guardrails) == PermissionMode.DEFAULT


def test_permission_decision_payload_denies_write_for_read_only_profile():
    guardrails = AgentGuardrails(tool_permission_profile="read_only")

    payload = _permission_decision_payload(
        {"tool_call_id": "call-write", "tool_call_name": "Write"},
        guardrails,
    )

    assert payload["resourceType"] == "tool"
    assert payload["resourceId"] == "Write"
    assert payload["action"] == "write"
    assert payload["decision"] == "deny"


def test_permission_decision_payload_maps_unknown_tool_to_mcp_execute():
    guardrails = AgentGuardrails(tool_permission_profile="trusted_tools")

    payload = _permission_decision_payload(
        {"tool_call_id": "call-mcp", "tool_call_name": "SearchDocs"},
        guardrails,
    )

    assert payload["resourceType"] == "mcp"
    assert payload["action"] == "execute"
    assert payload["decision"] == "allow"


def test_pending_hitl_tool_calls_seed_chat_history_tool_runs():
    runs = _pending_tool_calls_to_runs([
        {
            "id": "call-1",
            "name": "AskUser",
            "input": {
                "title": "补充信息",
                "questions": [{"id": "target", "type": "text", "label": "目标"}],
            },
        },
    ])

    assert runs == [
        ChatToolRun(
            id="call-1",
            name="AskUser",
            args='{"title": "补充信息", "questions": [{"id": "target", "type": "text", "label": "目标"}]}',
            status="running",
        ),
    ]


def test_pending_hitl_tool_calls_preserve_existing_tool_history():
    runs = _pending_tool_calls_to_runs(
        [
            {
                "id": "call-ask",
                "name": "AskUser",
                "input": {"title": "补充信息", "questions": []},
            },
        ],
        [
            ChatToolRun(id="call-read", name="Read", args='{"file_path": "a.md"}', status="completed"),
            ChatToolRun(id="call-grep", name="Grep", args='{"pattern": "todo"}', status="completed"),
        ],
    )

    assert [item.name for item in runs] == ["Read", "Grep", "AskUser"]
    assert runs[0].args == '{"file_path": "a.md"}'
    assert runs[2].args == '{"title": "补充信息", "questions": []}'


def test_background_run_keeps_waiting_status_after_hitl_required():
    async def run_waiting_case():
        async def factory():
            yield source_event("HITL_REQUIRED", {"id": "hitl-1"})

        run = await start_chat_background_run(
            session_id="session-waiting",
            factory=factory,
            task="需要人工确认的任务",
            persist=False,
        )
        assert run.async_task is not None
        await run.async_task
        return run

    run = asyncio.run(run_waiting_case())

    assert run.status == "waiting_user"
    assert run.completed_at is None


def test_run_waiting_keeps_subscriber_until_actionable_approval():
    """RUN_WAITING 只是预告，实时流必须继续交付随后的 approval Frame。"""

    async def run_case():
        release = asyncio.Event()

        async def factory():
            await release.wait()
            yield runtime_trace_source_event(
                "RUN_WAITING",
                session_id="session-actionable-waiting",
                scope="run",
                status="waiting_user",
                payload={"waitState": "waiting_user"},
            )
            await asyncio.sleep(0)
            yield source_event("HITL_REQUIRED", {
                "interactionId": "interaction-actionable",
                "type": "REQUIRE_EXTERNAL_EXECUTION",
                "toolCalls": [],
            })

        run = await start_chat_background_run(
            session_id="session-actionable-waiting",
            factory=factory,
            task="等待可操作审批",
            persist=False,
        )

        async def collect():
            frames: list[str] = []
            async for frame in subscribe_chat_background_run(run.id, after_seq=0):
                frames.append(frame)
                if '"id":"approval:interaction-actionable"' in frame:
                    break
            return frames

        collector = asyncio.create_task(collect())
        await asyncio.sleep(0)
        release.set()
        assert run.async_task is not None
        await run.async_task
        return run, await collector

    run, frames = asyncio.run(run_case())

    assert run.status == "waiting_user"
    assert any('"kind":"approval"' in frame for frame in frames)
    assert any('"id":"approval:interaction-actionable"' in frame for frame in frames)


def test_background_run_preserves_failed_status_after_run_failed_event():
    """RUN_FAILED 已经把后台 Run 标为 failed 时，消费结束不能覆盖成 completed。"""

    async def run_failed_case():
        async def factory():
            yield runtime_trace_source_event(
                "RUN_FAILED",
                session_id="session-run-failed",
                run_id="run-run-failed",
                scope="run",
                status="failed",
                reason="agent_error",
                payload={"message": "模型请求失败"},
            )
            yield source_event("ERROR", {"message": "模型请求失败"})

        run = await start_chat_background_run(
            session_id="session-run-failed",
            factory=factory,
            task="触发失败",
            persist=False,
        )
        assert run.async_task is not None
        await run.async_task
        return run

    run = asyncio.run(run_failed_case())

    assert run.status == "failed"
    assert run.completed_at is not None


def test_background_run_completes_after_plan_review_requested():
    """Plan revision 提交审核后，本轮起草 Run 正常完成。"""

    async def run_finalized_case():
        async def factory():
            yield runtime_trace_source_event(
                "PLAN_REVIEW_REQUESTED",
                session_id="session-plan-finalized",
                run_id="run-plan-finalized",
                scope="plan",
                status="waiting",
                payload={"planId": "plan-1", "status": "review_waiting", "revision": 1, "markdown": "# 计划"},
            )
            yield source_event("FINAL_ANSWER_DELTA", {"delta": "# 计划"})
            yield runtime_trace_source_event(
                "RUN_COMPLETED",
                session_id="session-plan-finalized",
                run_id="run-plan-finalized",
                scope="run",
                status="completed",
                payload={"resultLength": 4},
            )

        run = await start_chat_background_run(
            session_id="session-plan-finalized",
            factory=factory,
            task="制定计划",
            persist=False,
        )
        assert run.async_task is not None
        await run.async_task
        return run

    run = asyncio.run(run_finalized_case())

    assert run.status == "completed"
    assert run.completed_at is not None


def test_background_run_plan_review_tail_events_do_not_break_completed_status():
    """Plan review 提交后的工具尾事件不能把 Run 冲回 running。"""

    async def run_finalized_case():
        async def factory():
            yield runtime_trace_source_event(
                "PLAN_REVIEW_REQUESTED",
                session_id="session-plan-tail",
                run_id="run-plan-tail",
                scope="plan",
                status="waiting",
                payload={"planId": "plan-1", "status": "review_waiting", "revision": 1, "markdown": "# 计划"},
            )
            yield source_event("FINAL_ANSWER_DELTA", {"delta": "# 计划"})
            yield source_event("TOOL_RESULT_END", {
                "tool_call_id": "call-plan-review",
                "tool_call_name": "plan_request_review",
                "state": "success",
            })
            yield source_event("REPLY_END", {"reply_id": "reply-1"})
            yield runtime_trace_source_event(
                "RUN_COMPLETED",
                session_id="session-plan-tail",
                run_id="run-plan-tail",
                scope="run",
                status="completed",
                payload={"resultLength": 4},
            )

        run = await start_chat_background_run(
            session_id="session-plan-tail",
            factory=factory,
            task="制定计划",
            persist=False,
        )
        assert run.async_task is not None
        await run.async_task
        return run

    run = asyncio.run(run_finalized_case())

    assert run.status == "completed"
    assert run.completed_at is not None


def test_background_run_resume_relay_marks_original_run_completed(monkeypatch):
    persisted_snapshots: list[dict] = []
    monkeypatch.setattr(
        background_runs_module,
        "_update_latest_assistant_display_snapshot",
        lambda **kwargs: persisted_snapshots.append(kwargs["display_snapshot"]),
    )

    async def run_resume_case():
        async def initial_factory():
            yield source_event("HITL_REQUIRED", {"id": "hitl-1"})

        async def resume_factory():
            yield source_event("FINAL_ANSWER_DELTA", {"delta": "first"})
            yield source_event("FINAL_ANSWER_DELTA", {"delta": " second"})
            yield source_event("REPLY_END", {"reply_id": "reply-1"})

        run = await start_chat_background_run(
            session_id="session-resume",
            factory=initial_factory,
            task="需要人工确认的任务",
            persist=False,
        )
        subscriber: asyncio.Queue[str | None] = asyncio.Queue()
        run.subscribers.add(subscriber)
        assert run.async_task is not None
        await run.async_task
        assert run.status == "waiting_user"
        waiting_published: list[str | None] = []
        while not subscriber.empty():
            waiting_published.append(subscriber.get_nowait())
        assert None not in waiting_published

        replayed = [
            event
            async for event in relay_chat_background_run_events(run.id, resume_factory())
        ]
        published: list[str | None] = []
        while not subscriber.empty():
            published.append(subscriber.get_nowait())
        return run, replayed, published

    run, replayed, published = asyncio.run(run_resume_case())

    assert run.status == "completed"
    assert run.completed_at is not None
    assert len(replayed) == 7
    assert any('"id":"approval:hitl-1"' in frame for frame in replayed)
    assert any('"patch":"first"' in frame for frame in replayed)
    assert any('"patch":" second"' in frame for frame in replayed)
    assert any('"done":true' in frame for frame in replayed)
    assert published[-1] is None
    assert persisted_snapshots[-1]["streamDone"] is True
    assert all(block["phase"] == "closed" for block in persisted_snapshots[-1]["blocksById"].values())


def test_final_snapshot_replaces_same_run_waiting_placeholder(monkeypatch):
    """同一 Run 完成后，历史消息只能保留一棵最终 Block Tree。"""

    root_id = "message:run-resume-history"
    waiting_message = SimpleNamespace(
        id="waiting-placeholder",
        role="assistant",
        created_at="2026-07-23T00:00:01+00:00",
        display_snapshot={
            "version": 4,
            "lastSeq": 4,
            "streamDone": False,
            "order": [root_id],
            "blocksById": {
                root_id: {"id": root_id, "kind": "message", "phase": "open", "data": {}},
            },
        },
    )
    final_message = SimpleNamespace(
        id="final-answer",
        role="assistant",
        created_at="2026-07-23T00:00:02+00:00",
        display_snapshot={
            "version": 4,
            "lastSeq": 0,
            "streamDone": False,
            "order": [],
            "blocksById": {},
        },
    )
    session = SimpleNamespace(id="session-resume-history", message_count=2)
    store = SimpleNamespace(
        messages={"session-resume-history": [waiting_message, final_message]},
        sessions=[session],
    )
    saved: list[object] = []
    monkeypatch.setattr(chat_store_module, "_load_store", lambda: store)
    monkeypatch.setattr(chat_store_module, "_save_store", lambda value: saved.append(value))
    final_snapshot = {
        "version": 4,
        "lastSeq": 8,
        "streamDone": True,
        "order": [root_id],
        "blocksById": {
            root_id: {"id": root_id, "kind": "message", "phase": "closed", "data": {}},
        },
    }

    chat_store_module._update_latest_assistant_display_snapshot(
        session_id="session-resume-history",
        display_snapshot=final_snapshot,
        min_created_at="2026-07-23T00:00:00+00:00",
    )

    assert saved == [store]
    assert store.messages["session-resume-history"] == [final_message]
    assert final_message.display_snapshot == final_snapshot
    assert session.message_count == 1


def test_background_run_resume_relay_preserves_failed_terminal_status():
    """恢复流已发出 RUN_FAILED 时，relay 结束不得把终态覆盖成 completed。"""

    async def run_resume_failure_case():
        async def initial_factory():
            yield source_event("HITL_REQUIRED", {"id": "hitl-failed"})

        async def resume_factory():
            yield runtime_trace_source_event(
                "RUN_FAILED",
                session_id="session-resume-failed",
                run_id="",
                scope="run",
                status="failed",
                reason="resume_error",
                payload={"message": "恢复失败"},
            )
            yield source_event("ERROR", {"message": "恢复失败"})

        run = await start_chat_background_run(
            session_id="session-resume-failed",
            factory=initial_factory,
            task="恢复后失败的任务",
            persist=False,
        )
        assert run.async_task is not None
        await run.async_task
        async for _ in relay_chat_background_run_events(run.id, resume_factory()):
            pass
        return run

    run = asyncio.run(run_resume_failure_case())

    assert run.status == "failed"


def test_background_run_raw_error_finishes_as_failed():
    """即使调用方漏发 RUN_FAILED，ERROR 也必须把顶层 Run 收口为 failed。"""

    async def run_error_case():
        async def factory():
            yield source_event("ERROR", {"message": "直接失败"})

        run = await start_chat_background_run(
            session_id="session-raw-error",
            factory=factory,
            task="直接失败的任务",
            persist=False,
        )
        assert run.async_task is not None
        await run.async_task
        return run

    run = asyncio.run(run_error_case())

    assert run.status == "failed"
