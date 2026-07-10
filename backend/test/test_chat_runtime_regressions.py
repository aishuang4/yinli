import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from pydantic import ValidationError

from src.server.routes.chat.background_runs import (
    ChatBackgroundRun,
    _broadcast,
    mark_chat_background_run_resuming,
    relay_chat_background_run_events,
    start_chat_background_run,
)
from src.server.routes.chat.event_hub import (
    consume_run_state_display_events,
    initial_run_state,
    normalize_run_state,
    project_event,
)
from src.server.routes.chat.helpers import _sse_event
from src.server.routes.chat.hitl import PENDING_AGENTS, PENDING_INTERACTIONS, _build_resume_event, _create_pending_interaction
from src.server.routes.chat.handlers.resume import (
    _is_final_answer_text_scope,
    _should_emit_raw_resume_event,
    _should_wrap_resume_text_as_final,
    resume_interaction,
)
from src.server.routes.chat.handlers.runs import _build_rerun_chat_message
from src.server.routes.chat.intent_router import (
    IntentRoute,
    route_chat_message,
    route_chat_message_with_model,
    route_trace_payload,
)
from src.server.routes.chat.models import (
    ChatMessage,
    ChatResumeRequest,
    ChatToolRun,
)
from src.server.routes.chat.leader_stream import LeaderStreamState, stream_leader_agent_reply
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
from src.server.routes.chat.runtime_events import runtime_event_from_agentscope_sse, runtime_event_sse
from src.server.routes.chat.tool_scope_resolver import resolve_runtime_tool_scope
from src.server.routes.chat.tool_state import _pending_tool_calls_to_runs
from src.server.routes.chat.trace_recorder import _model_cache_from_event, _permission_decision_payload
from src.server.routes.traces import _event_trace_status, _trace_status
from src.agents.factory import AgentGuardrails, AgentModelOptions, RuntimeToolScope, _build_context_config
from src.security.permissions import (
    PermissionAction,
    PermissionDecision,
    ResourceType,
    build_permission_profile,
)
from src.tools.ask_user import AskUser
from src.server.routes.agent_builder import _default_agent_prompt


class _FakeAgentScopeEvent:
    def __init__(self, payload: dict):
        self.payload = payload

    def model_dump(self, mode: str = "json") -> dict:
        return dict(self.payload)


def _sse_payload(event: str) -> dict:
    _, data = event.split("data: ", 1)
    return json.loads(data)


def _final_answer_deltas(events: list[str]) -> list[str]:
    return [
        str(_sse_payload(event).get("delta") or "")
        for event in events
        if event.startswith("event: FINAL_ANSWER_DELTA")
    ]


def test_final_answer_prompt_strongly_requires_phase_marker():
    prompt = _build_final_answer_system_prompt()
    assert "FinalAnswerStart" in prompt
    assert "only valid way to finish a successful run" in prompt
    assert "A response that ends without this tool is invalid" in prompt


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

    async def _batch_tool_calls(self):
        return [SimpleNamespace(type="concurrent", tool_calls=[SimpleNamespace(id="call-recovery-final")])]

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


class _FakePlanExitAgent:
    plan_markdown = "# 计划\n\n- [ ] 修改文件\n- [ ] 验证前端流式输出\n- [ ] 总结结果"

    async def reply_stream(self, user_msg):
        yield _FakeAgentScopeEvent({
            "type": "TOOL_CALL_START",
            "reply_id": "reply-1",
            "tool_call_id": "call-plan-exit",
            "tool_call_name": "plan_exit",
        })
        yield _FakeAgentScopeEvent({
            "type": "TOOL_RESULT_START",
            "reply_id": "reply-1",
            "tool_call_id": "call-plan-exit",
            "tool_call_name": "plan_exit",
        })
        yield _FakeAgentScopeEvent({
            "type": "TOOL_RESULT_TEXT_DELTA",
            "reply_id": "reply-1",
            "tool_call_id": "call-plan-exit",
            "delta": json.dumps({
                "planId": "plan-1",
                "planFile": ".agent-workstation/plans/session-1/plan-1.md",
                "markdown": self.plan_markdown,
            }, ensure_ascii=False),
        })
        yield _FakeAgentScopeEvent({
            "type": "TOOL_RESULT_END",
            "reply_id": "reply-1",
            "tool_call_id": "call-plan-exit",
            "state": "success",
        })
        yield _FakeAgentScopeEvent({
            "type": "REPLY_END",
            "reply_id": "reply-1",
        })


class _FakeNativePlanFinalizedManager:
    """模拟 plan_exit 工具刚执行完、manager 先产出 PLAN_FINALIZED 的真实路径。"""

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
            "type": "PLAN_FINALIZED",
            "source": "runtime",
            "sourceEventType": None,
            "scope": "plan",
            "agentId": "agent-1",
            "agentName": "Agent",
            "status": "completed",
            "reason": "Plan finalized as final answer.",
            "timestamp": "2026-07-01T00:00:00+00:00",
            "durationMs": None,
            "payload": {
                "planId": "plan-1",
                "planFile": ".agent-workstation/plans/session-1/plan-1.md",
                "markdown": _FakePlanExitAgent.plan_markdown,
            },
        }]


class _FakePlanExitThenContinuesAgent(_FakePlanExitAgent):
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
            "tool_call_id": "call-write-after-plan-exit",
            "tool_call_name": "Write",
        })


class _FakeTodoWriteAgent:
    async def reply_stream(self, user_msg):
        yield _FakeAgentScopeEvent({
            "type": "TOOL_CALL_START",
            "reply_id": "reply-1",
            "tool_call_id": "call-todo-write",
            "tool_call_name": "todo_write",
        })
        yield _FakeAgentScopeEvent({
            "type": "TOOL_RESULT_START",
            "reply_id": "reply-1",
            "tool_call_id": "call-todo-write",
            "tool_call_name": "todo_write",
        })
        yield _FakeAgentScopeEvent({
            "type": "TOOL_RESULT_TEXT_DELTA",
            "reply_id": "reply-1",
            "tool_call_id": "call-todo-write",
            "delta": (
                '{"planId":"plan-1","planFile":".agent-workstation/plans/session-1/plan-1.md",'
                '"steps":[{"id":"plan-1-step-1","planId":"plan-1","stepIndex":0,'
                '"title":"补工具","description":"实现 todo_write","status":"pending",'
                '"assignedAgentId":null,"evidence":"","result":"","startedAt":null,'
                '"completedAt":null,"updatedAt":"2026-06-30T00:00:00+00:00"}]}'
            ),
        })
        yield _FakeAgentScopeEvent({
            "type": "TOOL_RESULT_END",
            "reply_id": "reply-1",
            "tool_call_id": "call-todo-write",
            "state": "success",
        })
        yield _FakeAgentScopeEvent({
            "type": "REPLY_END",
            "reply_id": "reply-1",
        })


class _FakeTaskOutputAgent:
    async def reply_stream(self, user_msg):
        yield _FakeAgentScopeEvent({
            "type": "TOOL_CALL_START",
            "reply_id": "reply-1",
            "tool_call_id": "call-task-output",
            "tool_call_name": "task_output",
        })
        yield _FakeAgentScopeEvent({
            "type": "TOOL_RESULT_START",
            "reply_id": "reply-1",
            "tool_call_id": "call-task-output",
            "tool_call_name": "task_output",
        })
        yield _FakeAgentScopeEvent({
            "type": "TOOL_RESULT_TEXT_DELTA",
            "reply_id": "reply-1",
            "tool_call_id": "call-task-output",
            "delta": (
                '{"planId":"plan-1","planFile":".agent-workstation/plans/session-1/plan-1.md",'
                '"step":{"id":"plan-1-step-1","planId":"plan-1","stepIndex":0,'
                '"title":"补工具","description":"实现 todo_write","status":"completed",'
                '"assignedAgentId":null,"evidence":"测试通过","result":"工具可用",'
                '"startedAt":null,"completedAt":"2026-06-30T00:01:00+00:00",'
                '"updatedAt":"2026-06-30T00:01:00+00:00"},'
                '"steps":[{"id":"plan-1-step-1","planId":"plan-1","stepIndex":0,'
                '"title":"补工具","description":"实现 todo_write","status":"completed",'
                '"assignedAgentId":null,"evidence":"测试通过","result":"工具可用",'
                '"startedAt":null,"completedAt":"2026-06-30T00:01:00+00:00",'
                '"updatedAt":"2026-06-30T00:01:00+00:00"}]}'
            ),
        })
        yield _FakeAgentScopeEvent({
            "type": "TOOL_RESULT_END",
            "reply_id": "reply-1",
            "tool_call_id": "call-task-output",
            "state": "success",
        })
        yield _FakeAgentScopeEvent({
            "type": "REPLY_END",
            "reply_id": "reply-1",
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


def test_run_state_projection_keeps_tool_result_as_summary():
    """RunState 只保留工具结果摘要，避免 Chat 主界面持有完整 Observation。"""

    run = _projection_run()
    state = initial_run_state(run)
    state = project_event(
        state,
        "TOOL_CALL_START",
        {"tool_call_id": "call-read", "tool_call_name": "Read"},
        run,
    )
    state = project_event(
        state,
        "TOOL_RESULT_TEXT_DELTA",
        {"tool_call_id": "call-read", "delta": "x" * 5000},
        run,
    )
    state = project_event(
        state,
        "TOOL_RESULT_END",
        {"tool_call_id": "call-read", "state": "success"},
        run,
    )

    tool = state["tools"][0]
    assert tool["name"] == "Read"
    assert tool["status"] == "completed"
    assert tool["outputSize"] == 5000
    assert tool["outputTruncated"] is True
    assert len(tool["outputPreview"]) == 4000


def test_run_state_projection_restores_final_answer_and_hitl():
    """RunState 能恢复最终答案与等待审批状态。"""

    run = _projection_run()
    state = project_event(initial_run_state(run), "FINAL_ANSWER_DELTA", {"delta": "完成"}, run)
    run.status = "waiting"
    state = project_event(
        state,
        "HITL_REQUIRED",
        {
            "interactionId": "hitl-1",
            "type": "tool_approval",
            "agentId": "agent-1",
            "agentName": "Agent",
            "toolCalls": [{"id": "call-bash", "name": "Bash"}],
        },
        run,
    )

    assert state["finalAnswer"] == "完成"
    assert state["status"] == "waiting"
    assert state["currentStage"] == "等待人工处理"
    assert state["approvals"][0]["interactionId"] == "hitl-1"


def test_run_state_projects_ask_user_into_process_timeline():
    """AskUser 反问和用户答案必须进入过程折叠面板。"""

    run = _projection_run()
    state = initial_run_state(run)
    run.status = "waiting"
    state = project_event(
        state,
        "HITL_REQUIRED",
        {
            "interactionId": "hitl-ask-1",
            "type": "REQUIRE_EXTERNAL_EXECUTION",
            "scope": "single",
            "agentId": "agent-1",
            "agentName": "Agent",
            "toolCalls": [{
                "id": "ask-call-1",
                "name": "AskUser",
                "input": (
                    '{"title":"需要目标文件","description":"缺少要修改的对象",'
                    '"questions":[{"id":"target","type":"text","label":"要修改哪个文件？"}]}'
                ),
            }],
        },
        run,
    )
    waiting_events = consume_run_state_display_events(state)

    waiting_item = state["displaySnapshot"]["actors"][0]["items"][0]
    assert waiting_item["event"] == "tool.started"
    assert waiting_item["status"] == "waiting"
    assert waiting_item["data"]["name"] == "AskUser"
    assert "模型反问：需要目标文件" in waiting_item["data"]["inputPreview"]
    assert waiting_item["data"]["outputPreview"] == ""
    feedback_block = next(block for block in state["displaySnapshot"]["blocks"] if block["block"] == "feedback")
    assert feedback_block["status"] == "waiting"
    assert feedback_block["data"]["approval"]["interactionId"] == "hitl-ask-1"
    assert [(event["block"], event["event"]) for event in waiting_events] == [
        ("process", "tool.started"),
        ("feedback", "feedback.waiting"),
    ]

    run.status = "running"
    state = project_event(
        state,
        "RUNTIME_EVENT",
        {
            "type": "HITL_RESOLVED",
            "status": "completed",
            "scope": "single",
            "payload": {
                "interactionId": "hitl-ask-1",
                "interactionType": "REQUIRE_EXTERNAL_EXECUTION",
                "action": "approve",
                "externalResultsPreview": {
                    "ask-call-1": "目标文件是 README.md",
                },
            },
        },
        run,
    )
    resolved_events = consume_run_state_display_events(state)

    actor_item = state["displaySnapshot"]["actors"][0]["items"][0]
    assert actor_item["event"] == "tool.completed"
    assert actor_item["status"] == "completed"
    assert "模型反问：需要目标文件" in actor_item["data"]["inputPreview"]
    assert "要修改哪个文件？" in actor_item["data"]["inputPreview"]
    assert actor_item["data"]["outputPreview"] == "目标文件是 README.md"
    assert [event["block"] for event in resolved_events] == ["process", "feedback"]
    assert resolved_events[0]["event"] == "tool.completed"
    assert resolved_events[1]["event"] == "feedback.resolved"


def test_run_state_schema_keeps_resolved_feedback_payload():
    """RunState schema 不能裁掉已提交的人类反馈结果。"""

    from src.server.routes.chat.run_state_schema import dump_run_state

    state = {
        "version": 2,
        "runId": "run-1",
        "sessionId": "session-1",
        "status": "running",
        "createdAt": "",
        "completedAt": None,
        "reasoning": "",
        "finalAnswer": "",
        "thinking": "",
        "tools": [],
        "skillCalls": [],
        "modelName": "",
        "tokenUsage": None,
        "approvals": [{
            "interactionId": "interaction-1",
            "kind": "clarification",
            "type": "clarification",
            "status": "resolved",
            "scope": "single",
            "resolvedAt": "2026-01-01T00:00:00+00:00",
            "action": "approve",
            "externalResultsPreview": {"answer": "用户反馈内容"},
            "createdAt": "",
        }],
        "plans": [],
        "displaySnapshot": {"version": 3, "mode": "single_agent", "actors": [], "blocks": []},
        "error": "",
    }

    approval = dump_run_state(state)["approvals"][0]

    assert approval["action"] == "approve"
    assert approval["resolvedAt"]
    assert approval["externalResultsPreview"]["answer"] == "用户反馈内容"


def test_run_state_projection_builds_single_agent_display_snapshot():
    """RunState 必须投影单 Agent 主展示快照，前端不再扫描 runtimeEvents 拼主界面。"""

    run = _projection_run()
    state = initial_run_state(run)
    state = project_event(state, "REASONING_DELTA", {"delta": "先分析"}, run)
    state = project_event(
        state,
        "TOOL_CALL_START",
        {"tool_call_id": "call-write", "tool_call_name": "Write"},
        run,
    )
    state = project_event(
        state,
        "TOOL_CALL_DELTA",
        {"tool_call_id": "call-write", "delta": '{"file_path":"docs/result.md"}'},
        run,
    )
    state = project_event(
        state,
        "TOOL_RESULT_END",
        {"tool_call_id": "call-write", "state": "success"},
        run,
    )
    state = project_event(state, "FINAL_ANSWER_DELTA", {"delta": "完成"}, run)

    actors = state["displaySnapshot"]["actors"]
    blocks = state["displaySnapshot"]["blocks"]
    assert state["displaySnapshot"]["mode"] == "single_agent"
    assert len(actors) == 1
    assert [block["block"] for block in blocks] == ["answer", "files"]

    actor = actors[0]
    assert actor["role"] == "single"
    assert actor["items"][0]["event"] == "reasoning.delta"
    assert actor["items"][0]["targetId"].startswith("reasoning-")
    assert actor["items"][0]["data"]["text"] == "先分析"
    assert actor["items"][1]["targetId"] == "call-write"

    final_answer = blocks[0]
    assert final_answer["id"] == "answer"
    assert final_answer["data"]["content"] == "完成"
    assert final_answer["data"]["started"] is True

    generated_files = blocks[1]
    assert generated_files["data"]["files"] == [
        {"path": "docs/result.md", "name": "result.md", "extension": "MD"}
    ]


def test_run_state_display_snapshot_keeps_only_current_human_feedback():
    """feedback 展示块只放当前等待项，不保留已 resolved 的历史交互。"""

    run = _projection_run()
    state = initial_run_state(run)
    state = project_event(
        state,
        "HITL_REQUIRED",
        {
            "interactionId": "hitl-1",
            "type": "tool_approval",
            "toolCalls": [{"id": "call-bash", "name": "Bash"}],
        },
        run,
    )
    state = project_event(
        state,
        "RUNTIME_EVENT",
        {
            "type": "HITL_RESOLVED",
            "status": "completed",
            "payload": {"interactionId": "hitl-1"},
        },
        run,
    )
    state = project_event(
        state,
        "CLARIFICATION_REQUIRED",
        {
            "id": "clarify-1",
            "title": "需要信息",
            "questions": [{"id": "q1", "type": "text"}],
        },
        run,
    )

    human_feedback = next(block for block in state["displaySnapshot"]["blocks"] if block["block"] == "feedback")
    assert human_feedback["data"]["approval"]["interactionId"] == "clarify-1"
    assert human_feedback["status"] == "waiting"


def test_run_state_process_timeline_splits_reasoning_around_tools():
    """reasoning -> tool -> reasoning 必须按发生顺序保留为独立 process items。"""

    run = _projection_run()
    state = initial_run_state(run)

    state = project_event(state, "REASONING_DELTA", {"delta": "先分析"}, run)
    first_reasoning_event = consume_run_state_display_events(state)[0]
    state = project_event(
        state,
        "TOOL_CALL_START",
        {"tool_call_id": "call-read", "tool_call_name": "Read"},
        run,
    )
    consume_run_state_display_events(state)
    state = project_event(
        state,
        "TOOL_CALL_DELTA",
        {"tool_call_id": "call-read", "delta": '{"file_path":"README.md"}'},
        run,
    )
    consume_run_state_display_events(state)
    state = project_event(
        state,
        "TOOL_RESULT_END",
        {"tool_call_id": "call-read", "state": "success"},
        run,
    )
    consume_run_state_display_events(state)
    state = project_event(state, "REASONING_DELTA", {"delta": "再分析"}, run)
    second_reasoning_event = consume_run_state_display_events(state)[0]

    actor = state["displaySnapshot"]["actors"][0]

    assert first_reasoning_event["targetId"] != second_reasoning_event["targetId"]
    assert [
        (item["event"], item["targetId"], item["data"].get("text") or item["data"].get("name"))
        for item in actor["items"]
    ] == [
        ("reasoning.delta", first_reasoning_event["targetId"], "先分析"),
        ("tool.completed", "call-read", "Read"),
        ("reasoning.delta", second_reasoning_event["targetId"], "再分析"),
    ]


def test_run_state_ignores_main_runtime_tool_when_raw_tool_event_follows():
    """主 Agent RuntimeEvent 与原始工具事件同源时，只展示原始工具过程项。"""

    run = _projection_run()
    state = initial_run_state(run)

    state = project_event(state, "RUNTIME_EVENT", {
        "type": "TOOL_CALL_STARTED",
        "scope": "single_agent",
        "agentId": "default",
        "agentName": "Assistant",
        "status": "running",
        "payload": {
            "tool_call_id": "call-read",
            "tool_call_name": "Read",
        },
    }, run)
    assert consume_run_state_display_events(state) == []

    state = project_event(state, "TOOL_CALL_START", {"tool_call_id": "call-read", "tool_call_name": "Read"}, run)
    consume_run_state_display_events(state)
    state = project_event(state, "TOOL_CALL_DELTA", {"tool_call_id": "call-read", "delta": '{"file_path":"README.md"}'}, run)
    consume_run_state_display_events(state)
    state = project_event(state, "TOOL_RESULT_TEXT_DELTA", {"tool_call_id": "call-read", "delta": "hello"}, run)
    consume_run_state_display_events(state)
    state = project_event(state, "RUNTIME_EVENT", {
        "type": "TOOL_RESULT_COMPLETED",
        "scope": "single_agent",
        "agentId": "default",
        "agentName": "Assistant",
        "status": "completed",
        "payload": {
            "tool_call_id": "call-read",
            "state": "success",
        },
    }, run)
    assert consume_run_state_display_events(state) == []
    state = project_event(state, "TOOL_RESULT_END", {"tool_call_id": "call-read", "state": "success"}, run)
    consume_run_state_display_events(state)

    actor_items = state["displaySnapshot"]["actors"][0]["items"]
    tool_items = [item for item in actor_items if item["event"].startswith("tool.")]
    assert len(tool_items) == 1
    assert tool_items[0]["targetId"] == "call-read"
    assert tool_items[0]["data"]["name"] == "Read"
    assert tool_items[0]["data"]["inputPreview"] == '{"file_path":"README.md"}'
    assert tool_items[0]["data"]["outputPreview"] == "hello"


def test_completed_background_run_persists_history_display_snapshot(tmp_path, monkeypatch):
    """Run 完成后，历史 assistant 消息必须保存可直接回放的 displaySnapshot。"""

    from src.server.routes.chat import store as chat_store
    from src.storage import database

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "agent_workstation.db")
    monkeypatch.setattr(database, "_initialized", False)

    run = ChatBackgroundRun(
        id="run-history-snapshot",
        session_id="session-history-snapshot",
        agent_id="agent-1",
        agent_name="Agent",
        task_text="测试历史快照",
        created_at="2026-01-01T00:00:00+00:00",
        persisted=True,
    )
    database.upsert_chat_run({
        "id": run.id,
        "sessionId": run.session_id,
        "agentId": run.agent_id,
        "agentName": run.agent_name,
        "task": run.task_text,
        "mode": run.mode,
        "status": run.status,
        "createdAt": run.created_at,
    })
    run.state = database.upsert_chat_run_state(run.id, initial_run_state(run))

    chat_store._append_user_message(
        session_id=run.session_id,
        agent_id=run.agent_id,
        agent_name=run.agent_name,
        mode="agent",
        model_name="model",
        content="请测试历史快照",
        images=[],
    )
    asyncio.run(_broadcast(run, _sse_event("REASONING_DELTA", {"delta": "先分析"})))
    asyncio.run(_broadcast(run, _sse_event(
        "TOOL_CALL_START",
        {"tool_call_id": "call-read", "tool_call_name": "Read"},
    )))
    asyncio.run(_broadcast(run, _sse_event(
        "TOOL_RESULT_END",
        {"tool_call_id": "call-read", "state": "success"},
    )))
    asyncio.run(_broadcast(run, _sse_event("FINAL_ANSWER_DELTA", {"delta": "完成"})))
    chat_store._append_assistant_message(
        session_id=run.session_id,
        agent_id=run.agent_id,
        agent_name=run.agent_name,
        model_name="model",
        content="完成",
        error=None,
    )
    asyncio.run(_broadcast(run, runtime_event_sse(
        "RUN_COMPLETED",
        run_id=run.id,
        session_id=run.session_id,
        scope="run",
        agent_id=run.agent_id,
        agent_name=run.agent_name,
        status="completed",
        payload={},
    )))

    store = chat_store._load_store()
    assistant = [message for message in store.messages[run.session_id] if message.role == "assistant"][-1]
    actors = assistant.display_snapshot["actors"]
    blocks = assistant.display_snapshot["blocks"]

    assert assistant.display_snapshot["mode"] == "single_agent"
    assert [block["block"] for block in blocks] == ["answer"]
    assert actors[0]["status"] == "completed"
    assert [item["event"] for item in actors[0]["items"]] == ["reasoning.delta", "tool.completed"]
    assert blocks[0]["data"]["content"] == "完成"


def test_run_state_projection_does_not_duplicate_runtime_text_delta():
    """Runtime 文本增量只记录事件，正文累计以稳定文本事件为准。"""

    run = _projection_run()
    state = initial_run_state(run)
    state = project_event(
        state,
        "RUNTIME_EVENT",
        {
            "type": "AGENT_FINAL_ANSWER_DELTA",
            "status": "running",
            "payload": {"delta": "结果"},
        },
        run,
    )
    state = project_event(state, "FINAL_ANSWER_DELTA", {"delta": "结果"}, run)

    assert state["finalAnswer"] == "结果"
    assert state["runtimeEvents"][0]["type"] == "AGENT_FINAL_ANSWER_DELTA"


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


def test_clarification_required_can_clear_mistaken_final_answer():
    """自然语言反问兜底必须移除误发的 answer block，并进入等待态。"""

    run = _projection_run()
    state = initial_run_state(run)
    state = project_event(state, "FINAL_ANSWER_DELTA", {"delta": "请问你具体想让我处理什么？"}, run)
    consume_run_state_display_events(state)
    run.status = "waiting"
    state = project_event(
        state,
        "CLARIFICATION_REQUIRED",
        {
            "id": "clarify-natural-1",
            "title": "需要补充任务内容",
            "questions": [{"id": "target", "type": "text", "label": "请说明你希望我具体处理什么内容？"}],
            "clearFinalAnswer": True,
        },
        run,
    )
    events = consume_run_state_display_events(state)

    assert state["status"] == "waiting"
    assert state["finalAnswer"] == ""
    assert [event["event"] for event in events] == ["answer.completed", "tool.started", "feedback.waiting"]
    assert events[0]["block"] == "answer"
    assert events[0]["op"] == "remove"
    assert state["displaySnapshot"]["actors"][0]["items"][0]["event"] == "tool.started"
    assert state["displaySnapshot"]["blocks"][0]["block"] == "feedback"


def test_run_state_snapshot_contract_does_not_expose_runtime_events():
    """RunState 对前端只暴露展示快照，不再携带内部 runtimeEvents。"""

    from src.server.routes.chat.run_state_schema import dump_run_state

    run = _projection_run()
    state = project_event(
        initial_run_state(run),
        "RUNTIME_EVENT",
        {
            "type": "AGENT_REASONING_DELTA",
            "status": "running",
            "payload": {"delta": "过程"},
        },
        run,
    )

    payload = dump_run_state(state)

    assert "runtimeEvents" not in payload
    assert state["runtimeEvents"][0]["type"] == "AGENT_REASONING_DELTA"


def test_run_state_projection_finalizes_open_tools_on_terminal_run():
    """Run 结束时未返回结果的工具不能继续留在 running 状态。"""

    run = _projection_run()
    state = project_event(
        initial_run_state(run),
        "TOOL_CALL_START",
        {"tool_call_id": "call-read", "tool_call_name": "Read"},
        run,
    )
    state = project_event(
        state,
        "TOOL_CALL_END",
        {"tool_call_id": "call-read", "tool_call_name": "Read"},
        run,
    )
    state = project_event(
        state,
        "RUNTIME_EVENT",
        {"type": "RUN_COMPLETED", "status": "completed", "payload": {}},
        run,
    )

    assert state["status"] == "completed"
    assert state["tools"][0]["status"] == "failed"
    assert "没有返回结果" in state["tools"][0]["error"]


def test_normalize_run_state_finalizes_stale_terminal_tools():
    """历史终态 RunState 即使残留 running 工具，读取快照时也会被归一化。"""

    state = {
        "status": "completed",
        "tools": [{
            "id": "call-read",
            "name": "Read",
            "status": "running",
            "error": "",
        }],
    }

    normalized = normalize_run_state(state)

    assert normalized["tools"][0]["status"] == "failed"
    assert state["tools"][0]["status"] == "running"


def test_run_state_projection_tracks_skill_lifecycle():
    """Skill 生命周期进入 RunState，前端不再直接消费 SKILL_CONTEXT/SKILL_END。"""

    run = _projection_run()
    state = project_event(
        initial_run_state(run),
        "SKILL_CONTEXT",
        {
            "skills": [{
                "id": "skill-1",
                "name": "代码审查",
                "description": "检查代码风险",
                "resourcePath": "skills/review/SKILL.md",
                "resources": ["skills/review/checklist.md"],
            }],
        },
        run,
    )
    state = project_event(
        state,
        "SKILL_END",
        {"skills": [{"id": "skill-1", "name": "代码审查"}], "status": "completed"},
        run,
    )

    assert state["skillCalls"][0]["id"] == "skill-1"
    assert state["skillCalls"][0]["name"] == "代码审查"
    assert state["skillCalls"][0]["status"] == "completed"
    assert state["skillCalls"][0]["resourcePath"] == "skills/review/SKILL.md"


def test_run_state_projection_tracks_model_usage():
    """模型调用与 token 用量进入 RunState，前端不再直接消费 MODEL_CALL_*。"""

    run = _projection_run()
    state = project_event(
        initial_run_state(run),
        "MODEL_CALL_START",
        {"model_name": "deepseek-chat"},
        run,
    )
    state = project_event(
        state,
        "MODEL_CALL_END",
        {
            "model_name": "deepseek-chat",
            "input_tokens": 100,
            "output_tokens": 20,
            "cache_input_tokens": 40,
            "cache_creation_input_tokens": 10,
        },
        run,
    )

    assert state["modelName"] == "deepseek-chat"
    assert state["tokenUsage"] == {"input": 100, "output": 20}
    assert "modelCaches" not in normalize_run_state(state)


def test_run_state_projection_preserves_clarification_questions():
    """Readiness 反问通过 RunState approval 恢复到前端 clarificationRequests。"""

    run = _projection_run()
    run.status = "waiting"
    state = project_event(
        initial_run_state(run),
        "CLARIFICATION_REQUIRED",
        {
            "id": "clarification-1",
            "title": "需要补充信息",
            "description": "请选择目标文件",
            "questions": [{
                "id": "target",
                "type": "text",
                "label": "目标文件",
                "options": [],
                "placeholder": "src/app.ts",
                "required": True,
            }],
        },
        run,
    )

    assert state["status"] == "waiting"
    assert state["approvals"][0]["type"] == "clarification"
    assert state["approvals"][0]["title"] == "需要补充信息"
    assert state["approvals"][0]["questions"][0]["id"] == "target"


def test_run_state_clarification_keeps_approval_kind_when_payload_has_type():
    """反问 payload 的 type 不能覆盖 approval 类型，避免前端误判为 HITL。"""

    run = _projection_run()
    run.status = "waiting"
    state = project_event(
        initial_run_state(run),
        "CLARIFICATION_REQUIRED",
        {
            "id": "clarification-typed",
            "type": "text",
            "title": "需要补充信息",
            "questions": [{
                "id": "target",
                "type": "text",
                "label": "目标文件",
            }],
        },
        run,
    )

    approval = state["approvals"][0]
    assert approval["kind"] == "clarification"
    assert approval["type"] == "clarification"
    assert approval["questions"][0]["type"] == "text"


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


def test_subscribe_background_run_can_skip_replay():
    """RunState 恢复后订阅 stream 可跳过历史事件，避免前端重复拼接文本。"""

    async def run_case():
        from src.server.routes.chat.background_runs import subscribe_chat_background_run

        run = ChatBackgroundRun(
            id="run-no-replay-1",
            session_id="session-no-replay-1",
            events=[_sse_event("FINAL_ANSWER_DELTA", {"delta": "旧内容"})],
        )
        from src.server.routes.chat import background_runs

        async with background_runs._runs_lock:
            background_runs._runs[run.id] = run

        stream = subscribe_chat_background_run(run.id, replay=False)
        try:
            first = await asyncio.wait_for(stream.__anext__(), timeout=0.05)
        except TimeoutError:
            first = ""
        finally:
            await stream.aclose()
            async with background_runs._runs_lock:
                background_runs._runs.pop(run.id, None)
        return first

    assert asyncio.run(run_case()) == ""


def test_subscribe_run_state_stream_emits_snapshot_and_patch_updates():
    """RunState stream 先发当前快照，随后发送轻量状态增量。"""

    async def run_case():
        from src.server.routes.chat import background_runs
        from src.server.routes.chat.background_runs import subscribe_chat_run_state

        run = ChatBackgroundRun(
            id="run-state-stream-1",
            session_id="session-state-stream-1",
            state={},
        )
        run.state = initial_run_state(run)
        async with background_runs._runs_lock:
            background_runs._runs[run.id] = run

        stream = subscribe_chat_run_state(run.id)
        try:
            first = await asyncio.wait_for(stream.__anext__(), timeout=0.1)
            await _broadcast(run, _sse_event("FINAL_ANSWER_DELTA", {"delta": "结果"}))
            second = await asyncio.wait_for(stream.__anext__(), timeout=0.1)
        finally:
            await stream.aclose()
            async with background_runs._runs_lock:
                background_runs._runs.pop(run.id, None)
        return first, second

    first, second = asyncio.run(run_case())
    assert "event: RUN_STATE" in first
    assert '"finalAnswer": ""' in first
    assert "event: RUN_STATE_PATCH" in second
    assert '"kind": "display_event"' in second
    assert '"block": "answer"' in second
    assert '"event": "answer.delta"' in second
    assert '"content": "结果"' in second


def test_subscribe_run_state_stream_closes_for_waiting_run():
    """等待用户输入的 RunState stream 首包恢复等待态后必须结束。"""

    async def run_case():
        from src.server.routes.chat import background_runs
        from src.server.routes.chat.background_runs import subscribe_chat_run_state

        run = ChatBackgroundRun(
            id="run-state-waiting-1",
            session_id="session-state-waiting-1",
            status="waiting",
            state={},
        )
        run.state = project_event(
            initial_run_state(run),
            "CLARIFICATION_REQUIRED",
            {
                "id": "clarification-1",
                "title": "需要补充信息",
                "questions": [{"id": "target", "type": "text", "label": "目标"}],
            },
            run,
        )
        async with background_runs._runs_lock:
            background_runs._runs[run.id] = run

        stream = subscribe_chat_run_state(run.id)
        try:
            first = await asyncio.wait_for(stream.__anext__(), timeout=0.1)
            try:
                second = await asyncio.wait_for(stream.__anext__(), timeout=0.05)
            except StopAsyncIteration:
                second = ""
        finally:
            await stream.aclose()
            async with background_runs._runs_lock:
                background_runs._runs.pop(run.id, None)
        return first, second

    first, second = asyncio.run(run_case())
    assert "event: RUN_STATE" in first
    assert '"status": "waiting"' in first
    assert second == ""


def test_subscribe_run_state_stream_closes_after_waiting_patch():
    """运行中订阅收到等待态 patch 后必须结束，避免前端一直转圈。"""

    async def run_case():
        from src.server.routes.chat import background_runs
        from src.server.routes.chat.background_runs import subscribe_chat_run_state

        run = ChatBackgroundRun(
            id="run-state-waiting-patch-1",
            session_id="session-state-waiting-patch-1",
            state={},
        )
        run.state = initial_run_state(run)
        async with background_runs._runs_lock:
            background_runs._runs[run.id] = run

        stream = subscribe_chat_run_state(run.id)
        try:
            first = await asyncio.wait_for(stream.__anext__(), timeout=0.1)
            await _broadcast(run, _sse_event("CLARIFICATION_REQUIRED", {
                "id": "clarification-1",
                "title": "需要补充信息",
                "questions": [{"id": "target", "type": "text", "label": "目标"}],
            }))
            second = await asyncio.wait_for(stream.__anext__(), timeout=0.1)
            third = await asyncio.wait_for(stream.__anext__(), timeout=0.1)
            try:
                fourth = await asyncio.wait_for(stream.__anext__(), timeout=0.05)
            except StopAsyncIteration:
                fourth = ""
        finally:
            await stream.aclose()
            async with background_runs._runs_lock:
                background_runs._runs.pop(run.id, None)
        return first, second, third, fourth

    first, second, third, fourth = asyncio.run(run_case())
    assert "event: RUN_STATE" in first
    assert "event: RUN_STATE_PATCH" in second
    assert '"status": "waiting"' in second
    assert '"tool.started"' in second
    assert "event: RUN_STATE_PATCH" in third
    assert '"status": "waiting"' in third
    assert '"approval"' in third
    assert fourth == ""


def test_run_state_stream_waits_for_actionable_hitl_patch_before_closing():
    """AGENT_WAITING_HUMAN 只是内部状态，不再向前端发送 runtime_event patch。"""

    async def run_case():
        run = ChatBackgroundRun(
            id="run-state-hitl-actionable-1",
            session_id="session-state-hitl-actionable-1",
            state={},
        )
        run.state = initial_run_state(run)
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        run.state_subscribers.add(queue)

        await _broadcast(run, runtime_event_sse(
            "AGENT_WAITING_HUMAN",
            session_id=run.session_id,
            run_id=run.id,
            scope="single",
            status="waiting",
            payload={"interactionId": "hitl-1"},
        ))
        try:
            runtime_patch = queue.get_nowait()
        except asyncio.QueueEmpty:
            runtime_patch = ""

        await _broadcast(run, _sse_event("HITL_REQUIRED", {
            "interactionId": "hitl-1",
            "type": "REQUIRE_USER_CONFIRM",
            "sessionId": run.session_id,
            "agentId": "agent-1",
            "agentName": "Agent",
            "toolCalls": [{"id": "call-bash", "name": "Bash"}],
        }))
        hitl_patch = await asyncio.wait_for(queue.get(), timeout=0.1)
        stream_close = await asyncio.wait_for(queue.get(), timeout=0.1)
        return runtime_patch, hitl_patch, stream_close

    runtime_patch, hitl_patch, stream_close = asyncio.run(run_case())
    assert runtime_patch == ""
    assert hitl_patch is not None
    assert "event: RUN_STATE_PATCH" in hitl_patch
    assert '"kind": "display_event"' in hitl_patch
    assert '"approval"' in hitl_patch
    assert stream_close is None


def test_mark_run_resuming_clears_actionable_hitl_before_state_resubscribe():
    """HITL resume/start 返回前必须清掉旧 waiting 快照，避免前端重订阅后立即关闭。"""

    async def run_case():
        from src.server.routes.chat import background_runs
        from src.server.routes.chat.background_runs import subscribe_chat_run_state

        run = ChatBackgroundRun(
            id="run-resume-race-1",
            session_id="session-resume-race-1",
            state={},
            status="waiting",
        )
        run.state = project_event(
            initial_run_state(run),
            "HITL_REQUIRED",
            {
                "interactionId": "hitl-resume-1",
                "type": "REQUIRE_EXTERNAL_EXECUTION",
                "sessionId": run.session_id,
                "agentId": "agent-1",
                "agentName": "Agent",
                "toolCalls": [{"id": "call-bash", "name": "Bash"}],
            },
            run,
        )
        async with background_runs._runs_lock:
            background_runs._runs[run.id] = run

        try:
            await mark_chat_background_run_resuming(
                run.id,
                interaction_id="hitl-resume-1",
                action="approve",
            )
            stream = subscribe_chat_run_state(run.id)
            try:
                first = await asyncio.wait_for(stream.__anext__(), timeout=0.1)
                try:
                    second = await asyncio.wait_for(stream.__anext__(), timeout=0.05)
                except TimeoutError:
                    second = ""
            finally:
                await stream.aclose()
            return first, second, run.state
        finally:
            async with background_runs._runs_lock:
                background_runs._runs.pop(run.id, None)

    first, second, state = asyncio.run(run_case())

    assert "event: RUN_STATE" in first
    assert '"status": "running"' in first
    assert '"status": "resolved"' in first
    assert second == ""
    assert state["status"] == "running"
    assert state["approvals"][0]["status"] == "resolved"


def test_run_state_patch_has_stable_kind_for_tool_plan_hitl_and_final_answer():
    """RunState patch 必须带稳定 kind，前端不能再靠猜字段分支。"""

    from src.server.routes.chat import background_runs

    run = _projection_run()
    run.state = initial_run_state(run)

    cases = [
        ("FINAL_ANSWER_DELTA", {"delta": "完成"}, "display_event"),
        ("TOOL_CALL_START", {"tool_call_id": "call-1", "tool_call_name": "Read"}, "display_event"),
        (
            "HITL_REQUIRED",
            {
                "interactionId": "hitl-1",
                "type": "REQUIRE_USER_CONFIRM",
                "toolCalls": [{"id": "call-bash", "name": "Bash"}],
            },
            "display_event",
        ),
        (
            "RUNTIME_EVENT",
            {
                "type": "PLAN_FINALIZED",
                "status": "completed",
                "payload": {
                    "planId": "plan-1",
                    "planFile": ".agent-workstation/plans/s/plan-1.md",
                    "markdown": "# 计划",
                },
            },
            "display_event",
        ),
    ]

    for event_type, data, expected_kind in cases:
        run.state = project_event(run.state, event_type, data, run)
        display_events = consume_run_state_display_events(run.state)
        patch = background_runs._run_state_patch_sse(
            run,
            event_type,
            data,
            display_event=display_events[0] if display_events else None,
        )
        assert f'"kind": "{expected_kind}"' in patch


def test_run_state_patch_sends_single_display_event():
    """RunState patch 只携带一个 displayEvent，前端用 reducer 聚合。"""

    from src.server.routes.chat import background_runs

    run = _projection_run()
    run.state = initial_run_state(run)
    run.state = project_event(run.state, "REASONING_DELTA", {"delta": "分析"}, run)
    run.state = project_event(run.state, "FINAL_ANSWER_DELTA", {"delta": "结果"}, run)
    display_events = consume_run_state_display_events(run.state)

    patch = background_runs._run_state_patch_sse(
        run,
        "FINAL_ANSWER_DELTA",
        {"delta": "结果"},
        display_event=display_events[0],
    )
    _, raw_data = patch.split("data: ", 1)
    data = __import__("json").loads(raw_data)

    assert "displayEvents" not in data
    assert data["displayEvent"]["block"] == "answer"
    assert data["displayEvent"]["event"] == "answer.delta"
    assert data["displayEvent"]["data"]["content"] == "结果"


def test_projector_event_matrix_for_run_state_v2_contract():
    """Projector 必须把核心事件矩阵稳定投影成 RunState v2。"""

    run = _projection_run()
    state = initial_run_state(run)
    events = [
        ("TOOL_CALL_START", {"tool_call_id": "call-read", "tool_call_name": "Read"}),
        ("TOOL_CALL_DELTA", {"tool_call_id": "call-read", "delta": '{"file_path":"README.md"}'}),
        ("TOOL_RESULT_TEXT_DELTA", {"tool_call_id": "call-read", "delta": "hello"}),
        ("TOOL_RESULT_END", {"tool_call_id": "call-read", "state": "success"}),
        ("FINAL_ANSWER_DELTA", {"delta": "最终答案"}),
        (
            "HITL_REQUIRED",
            {
                "interactionId": "hitl-1",
                "type": "REQUIRE_USER_CONFIRM",
                "toolCalls": [{"id": "call-bash", "name": "Bash"}],
            },
        ),
        (
            "CLARIFICATION_REQUIRED",
            {
                "id": "clarify-1",
                "title": "需要信息",
                "questions": [{"id": "q1", "type": "text"}],
            },
        ),
        (
            "RUNTIME_EVENT",
            {
                "type": "PLAN_TASKS_UPDATED",
                "status": "running",
                "payload": {
                    "planId": "plan-1",
                    "planFile": ".agent-workstation/plans/s/plan-1.md",
                    "steps": [{"id": "s1", "title": "测试", "status": "pending"}],
                },
            },
        ),
    ]
    for event_type, data in events:
        state = project_event(state, event_type, data, run)

    normalized = normalize_run_state(state)

    assert normalized["version"] == 2
    assert normalized["tools"][0]["id"] == "call-read"
    assert normalized["tools"][0]["status"] == "completed"
    assert normalized["finalAnswer"] == "最终答案"
    assert any(
        item["interactionId"] == "hitl-1" and item["kind"] == "hitl"
        for item in normalized["approvals"]
    )
    assert any(
        item["interactionId"] == "clarify-1" and item["kind"] == "clarification"
        for item in normalized["approvals"]
    )
    assert normalized["plans"][0]["planId"] == "plan-1"
    assert normalized["plans"][0]["steps"][0]["title"] == "测试"
    assert normalized["displaySnapshot"]["actors"][0]["items"][0]["event"] == "tool.completed"
    assert [block["block"] for block in normalized["displaySnapshot"]["blocks"]] == [
        "feedback",
        "plan",
        "answer",
    ]


def test_tool_result_runtime_event_preserves_failed_state():
    """工具结果失败时，流式 Runtime Event 应立即标记 failed。"""

    raw = runtime_event_from_agentscope_sse(
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
    _, data = raw.split("data: ", 1)
    event = __import__("json").loads(data)
    assert event["type"] == "TOOL_RESULT_COMPLETED"
    assert event["status"] == "failed"
    assert event["payload"]["state"] == "error"


def test_single_agent_hitl_pending_interaction_keeps_stream_scope():
    """单 Agent 人工审批恢复必须保持 single scope，避免后续工具流被误归到 leader。"""

    async def run_case():
        PENDING_INTERACTIONS.clear()
        PENDING_AGENTS.clear()
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
            )
        ]
        return events, list(PENDING_INTERACTIONS.values())

    events, interactions = asyncio.run(run_case())

    try:
        assert interactions
        assert interactions[0].scope == "single"
        assert any('"scope": "single"' in event for event in events if "HITL_REQUIRED" in event)
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
    assert any("SUBAGENT_COMPLETED" in event for event in events)


def test_single_agent_without_final_answer_marker_does_not_fallback_to_final_answer():
    """没有 FinalAnswerStart 时，过程文本只能留在 reasoning，不能兜底复制到最终答案。"""

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

    assert any(event.startswith("event: REASONING_DELTA") for event in events)
    assert not any(event.startswith("event: FINAL_ANSWER_DELTA") for event in events)
    assert not any("missing_final_answer_marker_fallback" in event for event in events)


def test_single_agent_recovers_missing_final_answer_marker_with_extra_call():
    """漏调 FinalAnswerStart 时，通过受限恢复轮重新生成最终答案。"""

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

    reasoning_events = [event for event in events if event.startswith("event: REASONING_DELTA")]
    final_events = [event for event in events if event.startswith("event: FINAL_ANSWER_DELTA")]
    assert any("工具后的过程总结" in event for event in reasoning_events)
    assert not any("工具后的过程总结" in event for event in final_events)
    assert any("恢复后的最终答案" in event for event in final_events)
    assert any("recover_missing_final_answer_marker" in event for event in events)


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

    assert any(event.startswith("event: REASONING_DELTA") for event in events)
    assert any("RUN_FAILED" in event for event in events)
    assert any(event.startswith("event: ERROR") for event in events)
    assert not any("RUN_COMPLETED" in event for event in events)


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

    assert any(event.startswith("event: REASONING_DELTA") for event in events)
    assert any('"reason": "max_react_iterations"' in event for event in events)
    assert any("RUN_FAILED" in event for event in events)
    assert any(event.startswith("event: ERROR") and "最大 ReAct 迭代次数" in event for event in events)
    assert not any("RUN_COMPLETED" in event for event in events)


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

    assert any(event.startswith("event: REASONING_DELTA") for event in events)
    assert any(event.startswith("event: FINAL_ANSWER_DELTA") and "恢复后的最终答案" in event for event in events)
    assert any("RUN_COMPLETED" in event for event in events)
    assert not any("RUN_FAILED" in event for event in events)
    assert not any(event.startswith("event: ERROR") for event in events)


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

    assert any(event.startswith("event: FINAL_ANSWER_DELTA") and "恢复后的最终答案" in event for event in events)
    assert any("RUN_COMPLETED" in event for event in events)
    assert not any("RUN_FAILED" in event for event in events)
    assert not any(event.startswith("event: ERROR") for event in events)
    assert "maximum iteration limit" in str(agent.recovery_message)


def test_single_agent_hitl_merges_same_reply_running_tool_calls():
    """同一模型回复里多个工具并发请求审批时，不能只保留第一个工具。"""

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
        hitl_events = [event for event in events if event.startswith("event: HITL_REQUIRED")]
        assert hitl_events
        assert "call-read-1" in hitl_events[0]
        assert "call-read-2" in hitl_events[0]
    finally:
        PENDING_INTERACTIONS.clear()
        PENDING_AGENTS.clear()


def test_resume_event_uses_only_agentscope_waiting_tool_calls():
    """恢复 AgentScope 审批时只回传真正等待确认的工具，UI 展示工具不能混入。"""

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
        resume_tool_calls=[
            {
                "id": "call-waiting",
                "name": "Read",
                "input": '{"file_path":"a.py"}',
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

        assert [item.tool_call.id for item in resume_event.confirm_results] == ["call-waiting"]
        assert [item["id"] for item in interaction.tool_calls] == ["call-waiting", "call-ui-only"]
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
        response = await resume_interaction(
            ChatResumeRequest.model_validate({
                "interactionId": interaction.id,
                "action": "approve",
            })
        )
        return [chunk async for chunk in response.body_iterator]

    events = asyncio.run(run_case())

    try:
        final_events = [
            event for event in events
            if event.startswith("event: FINAL_ANSWER_DELTA")
        ]
        assert not any("过程文本" in event for event in final_events)
        assert any("最终文本" in event for event in final_events)
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
        response = await resume_interaction(
            ChatResumeRequest.model_validate({
                "interactionId": interaction.id,
                "action": "approve",
            })
        )
        return [chunk async for chunk in response.body_iterator]

    events = asyncio.run(run_case())

    try:
        assert not any(event.startswith("event: FINAL_ANSWER_DELTA") for event in events)
        assert not any("missing_final_answer_marker_fallback" in event for event in events)
        assert any("RUN_FAILED" in event for event in events)
        assert any(event.startswith("event: ERROR") for event in events)
        assert not any("RUN_COMPLETED" in event for event in events)
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
        response = await resume_interaction(
            ChatResumeRequest.model_validate({
                "interactionId": interaction.id,
                "action": "approve",
            })
        )
        return [chunk async for chunk in response.body_iterator]

    events = asyncio.run(run_case())

    try:
        final_events = [
            event for event in events
            if event.startswith("event: FINAL_ANSWER_DELTA")
        ]
        assert not any("工具后的过程总结" in event for event in final_events)
        assert any("恢复后的最终答案" in event for event in final_events)
        assert any("RUN_COMPLETED" in event for event in events)
        assert not any("RUN_FAILED" in event for event in events)
    finally:
        PENDING_INTERACTIONS.clear()
        PENDING_AGENTS.clear()


def test_plan_exit_tool_result_becomes_final_answer():
    """plan_exit 工具结果必须转成最终答案，而不是等待审批事件。"""

    async def run_case():
        events = [
            event
            async for event in stream_leader_agent_reply(
                agent=_FakePlanExitAgent(),
                user_msg=object(),
                state=LeaderStreamState(),
                session_id="session-1",
                persist=False,
                agent_id="agent-1",
                agent_name="Agent",
                model_name="deepseek-chat",
                guardrails=AgentGuardrails(),
                message="制定计划",
                stream_scope="single",
                skill_runs=[],
            )
        ]
        return events

    events = asyncio.run(run_case())

    assert any('"type": "PLAN_FINALIZED"' in event for event in events)
    assert any(event.startswith("event: FINAL_ANSWER_DELTA") and "# 计划" in event for event in events)
    assert any('"planId": "plan-1"' in event for event in events)
    final_deltas = _final_answer_deltas(events)
    assert len(final_deltas) > 1
    assert "".join(final_deltas) == _FakePlanExitAgent.plan_markdown
    assert all(delta != _FakePlanExitAgent.plan_markdown for delta in final_deltas)


def test_plan_exit_stops_leader_stream_after_final_answer():
    """plan_exit 后必须输出计划最终答案并停止当前 Agent 流。"""

    async def run_case():
        state = LeaderStreamState()
        events = [
            event
            async for event in stream_leader_agent_reply(
                agent=_FakePlanExitThenContinuesAgent(),
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
            )
        ]
        return state, events

    state, events = asyncio.run(run_case())

    assert state.stopped is True
    assert state.stop_reason == "plan_finalized"
    assert any(event.startswith("event: FINAL_ANSWER_DELTA") and "# 计划" in event for event in events)
    assert "".join(_final_answer_deltas(events)) == _FakePlanExitAgent.plan_markdown
    assert not any("不应该继续输出" in event for event in events)
    assert not any("call-write-after-plan-exit" in event for event in events)


def test_native_plan_finalized_waits_for_tool_result_end_before_stopping():
    """manager 原生 PLAN_FINALIZED 必须等 plan_exit TOOL_RESULT_END 后再停流。"""

    async def run_case():
        state = LeaderStreamState()
        events = [
            event
            async for event in stream_leader_agent_reply(
                agent=_FakePlanExitThenContinuesAgent(),
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
                plan_mode_manager=_FakeNativePlanFinalizedManager(),
            )
        ]
        return state, events

    state, events = asyncio.run(run_case())

    tool_result_end_index = next(index for index, event in enumerate(events) if event.startswith("event: TOOL_RESULT_END"))
    final_answer_index = next(index for index, event in enumerate(events) if event.startswith("event: FINAL_ANSWER_DELTA"))
    assert tool_result_end_index < final_answer_index
    final_deltas = _final_answer_deltas(events)
    assert len(final_deltas) > 1
    assert "".join(final_deltas) == _FakePlanExitAgent.plan_markdown
    assert state.assistant_tool_calls[0].id == "call-plan-exit"
    assert state.assistant_tool_calls[0].status == "completed"
    assert state.stopped is True
    assert state.stop_reason == "plan_finalized"
    assert not any("call-write-after-plan-exit" in event for event in events)


def test_resume_plan_exit_tool_result_becomes_final_answer():
    """HITL resume 后执行 plan_exit，也必须输出最终答案并完成本轮。"""

    async def run_case():
        PENDING_INTERACTIONS.clear()
        PENDING_AGENTS.clear()
        interaction = _create_pending_interaction(
            interaction_type="REQUIRE_USER_CONFIRM",
            agent=_FakePlanExitAgent(),
            session_id="session-resume-plan",
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
            message="读取后制定计划",
            assistant_text="",
            skill_runs=[],
            scope="single",
            persist=False,
        )
        response = await resume_interaction(
            ChatResumeRequest.model_validate({
                "interactionId": interaction.id,
                "action": "approve",
            })
        )
        return [chunk async for chunk in response.body_iterator]

    events = asyncio.run(run_case())

    try:
        assert any('"type": "PLAN_FINALIZED"' in event for event in events)
        assert any(event.startswith("event: FINAL_ANSWER_DELTA") and "# 计划" in event for event in events)
        assert "".join(_final_answer_deltas(events)) == _FakePlanExitAgent.plan_markdown
        assert any('"planId": "plan-1"' in event for event in events)
        assert any("RUN_COMPLETED" in event for event in events)
    finally:
        PENDING_INTERACTIONS.clear()
        PENDING_AGENTS.clear()


def test_todo_write_tool_result_emits_plan_tasks_updated_runtime_event():
    """todo_write 工具结果必须实时转成结构化任务列表事件。"""

    async def run_case():
        return [
            event
            async for event in stream_leader_agent_reply(
                agent=_FakeTodoWriteAgent(),
                user_msg=object(),
                state=LeaderStreamState(),
                session_id="session-1",
                persist=False,
                agent_id="agent-1",
                agent_name="Agent",
                model_name="deepseek-chat",
                guardrails=AgentGuardrails(),
                message="制定结构化计划",
                stream_scope="single",
                skill_runs=[],
            )
        ]

    events = asyncio.run(run_case())

    assert any('"type": "PLAN_TASKS_UPDATED"' in event for event in events)
    assert any('"title": "补工具"' in event for event in events)


def test_task_output_tool_result_emits_plan_task_output_runtime_event():
    """task_output 工具结果必须实时转成步骤输出事件，并保留步骤状态。"""

    async def run_case():
        return [
            event
            async for event in stream_leader_agent_reply(
                agent=_FakeTaskOutputAgent(),
                user_msg=object(),
                state=LeaderStreamState(),
                session_id="session-1",
                persist=False,
                agent_id="agent-1",
                agent_name="Agent",
                model_name="deepseek-chat",
                guardrails=AgentGuardrails(),
                message="执行结构化计划",
                stream_scope="single",
                skill_runs=[],
            )
        ]

    events = asyncio.run(run_case())

    assert any('"type": "PLAN_TASK_OUTPUT"' in event for event in events)
    assert any('"status": "completed"' in event for event in events)
    assert any('"result": "工具可用"' in event for event in events)


def test_context_compression_schema_allows_project_discovery_summaries():
    context_config = _build_context_config()

    schema = context_config.summary_schema

    assert schema["properties"]["important_discoveries"]["maxLength"] >= 1000
    assert schema["properties"]["next_steps"]["maxLength"] >= 1000
    assert "summary" in schema["properties"]
    assert "required" not in schema
    assert "under the schema maxLength" in context_config.compression_prompt


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
    assert decision.scope.allow_bash is False
    assert decision.scope.allow_http is False


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
    assert decision.scope.allow_http is True
    assert decision.scope.allow_knowledge is True


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


def test_model_intent_route_falls_back_to_rules_on_invalid_json(monkeypatch):
    monkeypatch.setattr(
        "src.server.routes.chat.intent_router.get_or_create_agent",
        lambda **kwargs: _FakeRouterAgent("not json"),
    )

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

    assert scope.cache_key() == "mcp-0:knowledge-1:bash-0:http-1"


def test_runtime_builder_model_config_prefers_request_model():
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

    assert config.model_name == "request-model"
    assert config.fallback_names == ["fallback-a"]
    assert config.model_options.max_react_iters == 12


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
    assert "prompt-" in key


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
        "src.server.routes.chat.run_context.build_memory_injection_text",
        lambda context: "# Memory\n记忆注入片段",
    )

    ctx = asyncio.run(
        build_chat_run_context(
            ChatMessage(
                session_id="session-1",
                message="继续推进",
                agent_id="agent-1",
                agent_name="Agent",
                system_prompt="base prompt",
            ),
        ),
    )

    assert "base prompt" in ctx.system_prompt
    assert "# Memory\n记忆注入片段" in ctx.system_prompt


def test_runtime_builder_cache_key_changes_with_system_prompt():
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

    assert key_a != key_b


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
    assert "prompt-" in spec.runtime_agent_id


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

    assert WORKSPACE_DIR.endswith("agent-workstation")
    assert WORKSPACE_DIR != str(Path.home())


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


def test_trace_status_completed_after_waiting_is_resolved():
    status = _event_trace_status([
        {"eventType": "SESSION", "status": "running", "sortOrder": 0},
        {"eventType": "HITL_REQUIRED", "status": "waiting", "sortOrder": 1},
        {"eventType": "HITL_RESOLVED", "status": "completed", "sortOrder": 2},
        {"eventType": "ASSISTANT_MESSAGE", "status": "completed", "sortOrder": 3},
    ])

    assert status == "completed"


def test_trace_status_waiting_when_hitl_is_unresolved():
    status = _event_trace_status([
        {"eventType": "SESSION", "status": "running", "sortOrder": 0},
        {"eventType": "HITL_REQUIRED", "status": "waiting", "sortOrder": 1},
    ])

    assert status == "waiting"


def test_trace_status_does_not_complete_for_user_only_messages():
    status = _trace_status([
        SimpleNamespace(role="user", error=None, team_snapshot=None),
    ])

    assert status == "empty"


def test_trace_status_completes_when_assistant_message_exists():
    status = _trace_status([
        SimpleNamespace(role="user", error=None, team_snapshot=None),
        SimpleNamespace(role="assistant", error=None, team_snapshot=None),
    ])

    assert status == "completed"


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


def test_background_run_event_buffer_is_bounded(monkeypatch):
    import src.server.routes.chat.background_runs as background_runs

    monkeypatch.setattr(background_runs, "MAX_EVENTS_PER_RUN", 3)
    run = ChatBackgroundRun(id="run-test", session_id="session-test")

    async def emit_events():
        for index in range(5):
            await _broadcast(run, f"event-{index}")

    asyncio.run(emit_events())

    assert run.events == ["event-2", "event-3", "event-4"]


def test_background_run_keeps_waiting_status_after_hitl_required():
    async def run_waiting_case():
        async def factory():
            yield _sse_event("HITL_REQUIRED", {"id": "hitl-1"})

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

    assert run.status == "waiting"
    assert run.completed_at is None


def test_background_run_preserves_failed_status_after_run_failed_event():
    """RUN_FAILED 已经把后台 Run 标为 failed 时，消费结束不能覆盖成 completed。"""

    async def run_failed_case():
        async def factory():
            yield runtime_event_sse(
                "RUN_FAILED",
                session_id="session-run-failed",
                run_id="run-run-failed",
                scope="run",
                status="failed",
                reason="agent_error",
                payload={"message": "模型请求失败"},
            )
            yield _sse_event("ERROR", {"message": "模型请求失败"})

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
    assert run.state["status"] == "failed"


def test_background_run_completes_after_plan_finalized():
    """Plan finalized 是本轮正常完成，不再进入 waiting。"""

    async def run_finalized_case():
        async def factory():
            yield runtime_event_sse(
                "PLAN_FINALIZED",
                session_id="session-plan-finalized",
                run_id="run-plan-finalized",
                scope="plan",
                status="completed",
                payload={"planId": "plan-1", "markdown": "# 计划"},
            )
            yield _sse_event("FINAL_ANSWER_DELTA", {"delta": "# 计划"})
            yield runtime_event_sse(
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
    assert run.state["status"] == "completed"


def test_plan_finalized_runtime_event_updates_run_state():
    """PLAN_FINALIZED 必须投影为 completed plan block。"""

    run = _projection_run()
    state = project_event(
        initial_run_state(run),
        "RUNTIME_EVENT",
        {
            "id": "runtime-plan-finalized",
            "runId": run.id,
            "sessionId": run.session_id,
            "type": "PLAN_FINALIZED",
            "source": "runtime",
            "sourceEventType": None,
            "scope": "plan",
            "agentId": "agent-1",
            "agentName": "Agent",
            "status": "completed",
            "reason": "Plan finalized as final answer.",
            "timestamp": "2026-07-01T00:00:01+00:00",
            "durationMs": None,
            "payload": {
                "planId": "plan-1",
                "planFile": ".agent-workstation/plans/session/plan-1.md",
                "markdown": "# 计划",
            },
        },
        run,
    )

    assert state["plans"][0]["status"] == "completed"
    plan_block = next(block for block in state["displaySnapshot"]["blocks"] if block["block"] == "plan")
    assert plan_block["status"] == "completed"
    assert plan_block["data"]["planId"] == "plan-1"


def test_background_run_plan_tail_events_do_not_break_completed_status():
    """Plan finalized 后的工具尾事件不能把 Run 冲回 running。"""

    async def run_finalized_case():
        async def factory():
            yield runtime_event_sse(
                "PLAN_FINALIZED",
                session_id="session-plan-tail",
                run_id="run-plan-tail",
                scope="plan",
                status="completed",
                payload={"planId": "plan-1", "markdown": "# 计划"},
            )
            yield _sse_event("FINAL_ANSWER_DELTA", {"delta": "# 计划"})
            yield _sse_event("TOOL_RESULT_END", {
                "tool_call_id": "call-plan-exit",
                "tool_call_name": "plan_exit",
                "state": "success",
            })
            yield _sse_event("REPLY_END", {"reply_id": "reply-1"})
            yield runtime_event_sse(
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
    assert run.state["status"] == "completed"


def test_background_run_resume_relay_marks_original_run_completed():
    async def run_resume_case():
        async def initial_factory():
            yield _sse_event("HITL_REQUIRED", {"id": "hitl-1"})

        async def resume_factory():
            yield _sse_event("TEXT_BLOCK_DELTA", {"block_id": "block-1", "delta": "done"})
            yield _sse_event("REPLY_END", {"reply_id": "reply-1"})

        run = await start_chat_background_run(
            session_id="session-resume",
            factory=initial_factory,
            task="需要人工确认的任务",
            persist=False,
        )
        assert run.async_task is not None
        await run.async_task
        assert run.status == "waiting"

        replayed = [
            event
            async for event in relay_chat_background_run_events(run.id, resume_factory())
        ]
        return run, replayed

    run, replayed = asyncio.run(run_resume_case())

    assert run.status == "completed"
    assert run.completed_at is not None
    assert len(replayed) == 2
    assert any("TEXT_BLOCK_DELTA" in event for event in run.events)
