import asyncio
from pathlib import Path
from types import SimpleNamespace

from src.server.routes.chat.background_runs import (
    ChatBackgroundRun,
    _broadcast,
    mark_chat_background_run_resuming,
    relay_chat_background_run_events,
    start_chat_background_run,
)
from src.server.routes.chat.event_hub import initial_run_state, normalize_run_state, project_event
from src.server.routes.chat.helpers import _sse_event
from src.server.routes.chat.hitl import PENDING_AGENTS, PENDING_INTERACTIONS, _build_resume_event, _create_pending_interaction
from src.server.routes.chat.handlers.resume import (
    _is_final_answer_text_scope,
    _should_emit_raw_resume_event,
    _should_wrap_resume_text_as_final,
    resume_interaction,
)
from src.server.routes.chat.intent_router import (
    IntentRoute,
    route_chat_message,
    route_chat_message_with_model,
    route_trace_payload,
)
from src.server.routes.chat.models import (
    ChatMessage,
    ChatResumeRequest,
    ChatTeamActivity,
    ChatTeamMemberPayload,
    ChatToolRun,
)
from src.server.routes.chat.leader_stream import LeaderStreamState, stream_leader_agent_reply
from src.server.routes.chat.prompts import build_worker_assignment_text
from src.server.routes.chat.runtime import _permission_mode_for_guardrails
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
from src.server.routes.chat.team_worker_selector import filter_team_members_for_runtime
from src.server.routes.chat.tool_scope_resolver import resolve_runtime_tool_scope
from src.server.routes.chat.team_state import _collect_worker_outputs, _pending_tool_calls_to_runs
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


class _FakeAgentScopeEvent:
    def __init__(self, payload: dict):
        self.payload = payload

    def model_dump(self, mode: str = "json") -> dict:
        return dict(self.payload)


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


class _FakePlanExitAgent:
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
            "delta": (
                '{"planId":"plan-1","planFile":".agent-workstation/plans/session-1/plan-1.md",'
                '"waitingApproval":true,"markdown":"# 计划\\n\\n- [ ] 修改文件"}'
            ),
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


def test_run_state_projection_tracks_team_activity_snapshot():
    """Team 活动快照进入 RunState，前端不再依赖原始 TEAM_ACTIVITY 事件。"""

    run = _projection_run()
    state = project_event(
        initial_run_state(run),
        "TEAM_ACTIVITY",
        {
            "activities": [{
                "id": "activity-1",
                "agentId": "worker-1",
                "agentName": "Worker",
                "role": "worker",
                "status": "running",
                "task": "读取文件",
            }],
        },
        run,
    )

    assert state["teamActivities"][0]["id"] == "activity-1"
    assert state["teamActivities"][0]["status"] == "running"


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
    """模型调用与 token/cache 用量进入 RunState，前端不再直接消费 MODEL_CALL_*。"""

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
    assert state["modelCaches"][0]["cacheInputTokens"] == 40
    assert state["modelCaches"][0]["cacheCreationInputTokens"] == 10


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
    assert '"finalAnswerDelta": "结果"' in second


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
            try:
                third = await asyncio.wait_for(stream.__anext__(), timeout=0.05)
            except StopAsyncIteration:
                third = ""
        finally:
            await stream.aclose()
            async with background_runs._runs_lock:
                background_runs._runs.pop(run.id, None)
        return first, second, third

    first, second, third = asyncio.run(run_case())
    assert "event: RUN_STATE" in first
    assert "event: RUN_STATE_PATCH" in second
    assert '"status": "waiting"' in second
    assert '"approval"' in second
    assert third == ""


def test_run_state_stream_waits_for_actionable_hitl_patch_before_closing():
    """AGENT_WAITING_HUMAN 只是预告等待，必须等 HITL_REQUIRED 携带 approval 后再收口。"""

    async def run_case():
        from src.server.routes.chat import background_runs
        from src.server.routes.chat.background_runs import subscribe_chat_run_state

        run = ChatBackgroundRun(
            id="run-state-hitl-actionable-1",
            session_id="session-state-hitl-actionable-1",
            state={},
        )
        run.state = initial_run_state(run)
        async with background_runs._runs_lock:
            background_runs._runs[run.id] = run

        stream = subscribe_chat_run_state(run.id)
        try:
            first = await asyncio.wait_for(stream.__anext__(), timeout=0.1)
            await _broadcast(run, runtime_event_sse(
                "AGENT_WAITING_HUMAN",
                session_id=run.session_id,
                run_id=run.id,
                scope="single",
                status="waiting",
                payload={"interactionId": "hitl-1"},
            ))
            second = await asyncio.wait_for(stream.__anext__(), timeout=0.1)
            await _broadcast(run, _sse_event("HITL_REQUIRED", {
                "interactionId": "hitl-1",
                "type": "REQUIRE_USER_CONFIRM",
                "sessionId": run.session_id,
                "agentId": "agent-1",
                "agentName": "Agent",
                "toolCalls": [{"id": "call-bash", "name": "Bash"}],
            }))
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
    assert '"runtimeEvent"' in second
    assert '"approval"' not in second
    assert "event: RUN_STATE_PATCH" in third
    assert '"approval"' in third
    assert fourth == ""


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
        ("FINAL_ANSWER_DELTA", {"delta": "完成"}, "final_answer_delta"),
        ("TOOL_CALL_START", {"tool_call_id": "call-1", "tool_call_name": "Read"}, "tool"),
        (
            "HITL_REQUIRED",
            {
                "interactionId": "hitl-1",
                "type": "REQUIRE_USER_CONFIRM",
                "toolCalls": [{"id": "call-bash", "name": "Bash"}],
            },
            "approval",
        ),
        (
            "RUNTIME_EVENT",
            {
                "type": "PLAN_WAITING_APPROVAL",
                "status": "waiting",
                "payload": {"planId": "plan-1", "planFile": ".agent-workstation/plans/s/plan-1.md"},
            },
            "plans",
        ),
    ]

    for event_type, data, expected_kind in cases:
        run.state = project_event(run.state, event_type, data, run)
        patch = background_runs._run_state_patch_sse(run, event_type, data)
        assert f'"kind": "{expected_kind}"' in patch


def test_projector_event_matrix_for_run_state_v1_contract():
    """Projector 必须把核心事件矩阵稳定投影成 RunState v1。"""

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

    assert normalized["version"] == 1
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
                team_activities=[],
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
                team_activities=[],
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
        team_activities=[],
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
            team_activities=[],
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


def test_plan_exit_tool_result_emits_waiting_approval_runtime_event():
    """plan_exit 工具结果必须实时转成 PLAN_WAITING_APPROVAL，避免刷新后才出现审批入口。"""

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
                team_activities=[],
            )
        ]
        return events

    events = asyncio.run(run_case())

    assert any('"type": "PLAN_WAITING_APPROVAL"' in event for event in events)
    assert any('"planId": "plan-1"' in event for event in events)


def test_resume_plan_exit_tool_result_emits_waiting_approval_runtime_event():
    """HITL resume 后执行 plan_exit，也必须实时显示计划审批入口。"""

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
            team_activities=[],
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
        assert any('"type": "PLAN_WAITING_APPROVAL"' in event for event in events)
        assert any('"planId": "plan-1"' in event for event in events)
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
                team_activities=[],
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
                team_activities=[],
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
    assert _is_final_answer_text_scope("leader") is True
    assert _is_final_answer_text_scope("worker") is False
    assert _should_wrap_resume_text_as_final("TEXT_BLOCK_DELTA", "leader", True) is True
    assert _should_wrap_resume_text_as_final("TEXT_BLOCK_DELTA", "leader", False) is False
    assert _should_wrap_resume_text_as_final("TEXT_BLOCK_DELTA", "worker", True) is False


def test_team_leader_collects_formal_worker_report_only():
    """Leader 汇总只消费 Worker report，不读取运行过程 output 碎片。"""

    outputs = _collect_worker_outputs([
        ChatTeamActivity(
            id="activity-worker",
            agentId="worker-a",
            agentName="调研 Worker",
            role="worker",
            status="completed",
            task="调研任务",
            output="过程碎片：正在读取资料...",
            report="正式报告：用户流程已梳理完成。",
            createdAt="2026-06-29T00:00:00+00:00",
        ),
    ])

    assert outputs == [{
        "agentId": "worker-a",
        "name": "调研 Worker",
        "status": "completed",
        "detail": "",
        "report": "正式报告：用户流程已梳理完成。",
        "output": "正式报告：用户流程已梳理完成。",
        "skippedReason": "",
        "reportReviewStatus": "",
        "reportReviewNotes": "",
        "reportReviewGaps": "",
    }]
    assert _should_emit_raw_resume_event("TEXT_BLOCK_DELTA", "leader") is False
    assert _should_emit_raw_resume_event("TEXT_BLOCK_DELTA", "worker") is False
    assert _should_emit_raw_resume_event("THINKING_BLOCK_DELTA", "leader") is False
    assert _should_emit_raw_resume_event("THINKING_BLOCK_DELTA", "single") is True


def test_worker_assignment_uses_member_identity_when_description_is_default():
    research = ChatTeamMemberPayload.model_validate({
        "agentId": "research-agent",
        "name": "调研 Agent",
        "role": "worker",
        "description": "Team worker session",
    })
    developer = ChatTeamMemberPayload.model_validate({
        "agentId": "dev-agent",
        "name": "开发 Agent",
        "role": "worker",
        "description": "Team worker session",
    })

    research_assignment = build_worker_assignment_text(research)
    developer_assignment = build_worker_assignment_text(developer)

    assert research_assignment != developer_assignment
    assert "调研" in research_assignment or "事实" in research_assignment
    assert "实现" in developer_assignment or "代码" in developer_assignment


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
    route = route_chat_message(team_members=[])

    assert route.mode == "react"
    assert route.runtime.kind == "react"
    assert route.executor.kind == "single_agent"
    assert not hasattr(route, "selected_subagent_ids")
    assert not hasattr(route, "tool_scope")


def test_intent_route_trace_payload_uses_api_aliases():
    route = route_chat_message(team_members=[])

    payload = route_trace_payload(route)

    assert payload["mode"] == "react"
    assert payload["runtime"]["kind"] == "react"
    assert payload["executor"]["kind"] == "single_agent"
    assert "requireClarification" not in payload
    assert "selectedSkillIds" not in payload
    assert "selectedSubagentIds" not in payload


def test_intent_route_team_executor_is_mechanical():
    route = route_chat_message(
        team_members=[
            ChatTeamMemberPayload(
                id="member-worker",
                agentId="worker-agent",
                name="Worker",
                role="worker",
            ),
        ],
    )

    assert route.mode == "react"
    assert route.executor.kind == "team"
    assert route.executor.payload["memberCount"] == 1


def test_filter_team_members_for_runtime_keeps_leader_and_selected_workers():
    members = [
        ChatTeamMemberPayload(
            id="member-leader",
            agentId="leader-agent",
            name="Leader",
            role="leader",
            description="",
            executionMode="serial",
            dependsOn=[],
            handoffPrompt="",
        ),
        ChatTeamMemberPayload(
            id="member-research",
            agentId="research-agent",
            name="Research",
            role="worker",
            description="",
            executionMode="parallel",
            dependsOn=[],
            handoffPrompt="",
        ),
        ChatTeamMemberPayload(
            id="member-code",
            agentId="code-agent",
            name="Code",
            role="worker",
            description="",
            executionMode="parallel",
            dependsOn=[],
            handoffPrompt="",
        ),
    ]

    routed = filter_team_members_for_runtime(
        members=members,
        selected_member_ids=["research-agent"],
        leader_agent_id="leader-agent",
    )

    assert [member.agent_id for member in routed] == ["leader-agent", "research-agent"]


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
        callable_agents=["code-agent"],
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
        team_members=[],
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
            callable_agents=[],
            plan_config=SimpleNamespace(enabled=True),
            mcp_tools=[],
            knowledge_tools=[],
        ),
        team_members=[],
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
    assert spec.tool_scope is tool_scope
    assert "thinking-True" in spec.runtime_agent_id


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
        SimpleNamespace(role="user", error=None, team_activities=[]),
    ])

    assert status == "empty"


def test_trace_status_completes_when_assistant_message_exists():
    status = _trace_status([
        SimpleNamespace(role="user", error=None, team_activities=[]),
        SimpleNamespace(role="assistant", error=None, team_activities=[]),
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


def test_background_run_keeps_waiting_status_after_plan_approval_required():
    """Plan 等待审批必须进入 waiting，避免审批入口只靠刷新历史消息恢复。"""

    async def run_waiting_case():
        async def factory():
            yield runtime_event_sse(
                "PLAN_WAITING_APPROVAL",
                session_id="session-plan-waiting",
                run_id="run-plan-waiting",
                scope="plan",
                status="waiting",
                payload={"planId": "plan-1", "markdown": "# 计划"},
            )

        run = await start_chat_background_run(
            session_id="session-plan-waiting",
            factory=factory,
            task="制定计划",
            persist=False,
        )
        assert run.async_task is not None
        await run.async_task
        return run

    run = asyncio.run(run_waiting_case())

    assert run.status == "waiting"
    assert run.completed_at is None


def test_background_run_waiting_status_survives_plan_tail_events():
    """Plan 等待审批后的工具尾事件不能把 Run 冲回 running/completed。"""

    async def run_waiting_case():
        async def factory():
            yield runtime_event_sse(
                "PLAN_WAITING_APPROVAL",
                session_id="session-plan-tail",
                run_id="run-plan-tail",
                scope="plan",
                status="waiting",
                payload={"planId": "plan-1", "markdown": "# 计划"},
            )
            yield _sse_event("TOOL_RESULT_END", {
                "tool_call_id": "call-plan-exit",
                "tool_call_name": "plan_exit",
                "state": "success",
            })
            yield _sse_event("REPLY_END", {"reply_id": "reply-1"})

        run = await start_chat_background_run(
            session_id="session-plan-tail",
            factory=factory,
            task="制定计划",
            persist=False,
        )
        assert run.async_task is not None
        await run.async_task
        return run

    run = asyncio.run(run_waiting_case())

    assert run.status == "waiting"
    assert run.completed_at is None
    assert run.state["status"] == "waiting"


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
