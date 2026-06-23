"""Subagent 工具封装。"""

import json
import re
import time
from typing import Any, Callable

from agentscope.message import TextBlock, ToolResultState, UserMsg
from agentscope.permission import PermissionBehavior, PermissionContext, PermissionDecision
from agentscope.tool import ToolBase, ToolChunk

from src.agents.runtime_types import AgentGuardrails, SubagentRuntimeContext, SubagentRuntimeSpec
from src.security.permissions import ApprovalPolicy, UnifiedPermissionProfile, build_permission_profile


SUBAGENT_MIN_OUTPUT_CHARS = 160


def trim_tool_text(value: Any, limit: int = 8000) -> str:
    """裁剪工具输出，避免把超长子 Agent 回复塞回主 Agent 上下文。"""

    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n...[truncated {len(text) - limit} chars]"


def _safe_tool_suffix(name: str) -> str:
    """把 Agent 名称转换为 AgentScope 工具名后缀。"""

    suffix = re.sub(r"[^a-zA-Z0-9_]", "_", name).strip("_") or "Subagent"
    return suffix[:48]


def _is_incomplete_subagent_output(output: str, *, task: str, expected_output: str) -> bool:
    """判断子 Agent 是否只输出了准备性话语而没有真正交付结果。"""

    text = output.strip()
    if not text:
        return True
    normalized = re.sub(r"\s+", "", text.lower())
    opener_markers = [
        "我来分析",
        "我将分析",
        "我会分析",
        "我来处理",
        "我将开始",
        "下面我来",
        "接下来我",
        "letmeanalyze",
        "iwillanalyze",
    ]
    if len(text) < SUBAGENT_MIN_OUTPUT_CHARS and any(marker in normalized for marker in opener_markers):
        return True

    expected = expected_output.strip()
    if expected and len(text) < SUBAGENT_MIN_OUTPUT_CHARS:
        return True

    task_markers = [item for item in re.split(r"[，,。；;\n\s]+", task) if len(item) >= 4]
    if len(text) < SUBAGENT_MIN_OUTPUT_CHARS and task_markers:
        matched = sum(1 for marker in task_markers[:8] if marker in text)
        if matched <= 1:
            return True
    return False


def _is_read_only_guardrails(guardrails: AgentGuardrails | None) -> bool:
    profile = guardrails.permission_profile if guardrails else UnifiedPermissionProfile()
    return profile.tool_policy == ApprovalPolicy.READ_ONLY


def restrict_child_guardrails(parent: AgentGuardrails | None, child: AgentGuardrails) -> AgentGuardrails:
    """按主 Agent 安全边界收紧子 Agent 权限。"""

    profile_name = child.tool_permission_profile
    if _is_read_only_guardrails(parent):
        profile_name = "read_only"

    network_enabled = child.network_enabled and bool(parent.network_enabled if parent else True)
    execution_backend = child.execution_backend
    filesystem_scope = child.filesystem_scope
    docker_image = child.docker_image
    persistent_sandbox = child.persistent_sandbox
    if parent and parent.execution_backend == "docker":
        execution_backend = "docker"
        filesystem_scope = parent.filesystem_scope
        docker_image = parent.docker_image
        persistent_sandbox = parent.persistent_sandbox

    restricted = AgentGuardrails(
        filesystem_scope=filesystem_scope,
        shell_blacklist=list({*child.shell_blacklist, *(parent.shell_blacklist if parent else [])}),
        network_enabled=network_enabled,
        human_approval=child.human_approval or bool(parent.human_approval if parent else False),
        tool_permission_mode=child.tool_permission_mode,
        tool_permission_profile=profile_name,
        execution_backend=execution_backend,
        docker_image=docker_image,
        persistent_sandbox=persistent_sandbox,
        timeout_seconds=min(
            max(1, child.timeout_seconds),
            max(1, parent.timeout_seconds if parent else child.timeout_seconds),
        ),
    )
    return AgentGuardrails(
        filesystem_scope=restricted.filesystem_scope,
        shell_blacklist=restricted.shell_blacklist,
        network_enabled=restricted.network_enabled,
        human_approval=restricted.human_approval,
        tool_permission_mode=restricted.tool_permission_mode,
        tool_permission_profile=restricted.tool_permission_profile,
        execution_backend=restricted.execution_backend,
        docker_image=restricted.docker_image,
        persistent_sandbox=restricted.persistent_sandbox,
        timeout_seconds=restricted.timeout_seconds,
        permission_profile=build_permission_profile(restricted),
    )


class CallAgentTool(ToolBase):
    """把一个子 Agent 暴露为主 Agent 可调用工具。"""

    is_mcp: bool = False
    is_concurrency_safe: bool = False
    is_read_only: bool = True
    is_external_tool: bool = False
    is_state_injected: bool = False

    def __init__(
        self,
        *,
        parent_agent_id: str,
        parent_agent_name: str,
        spec: SubagentRuntimeSpec,
        parent_guardrails: AgentGuardrails | None,
        context: SubagentRuntimeContext,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        output_limit: int = 8000,
    ) -> None:
        self.parent_agent_id = parent_agent_id
        self.parent_agent_name = parent_agent_name
        self.spec = spec
        self.parent_guardrails = parent_guardrails
        self.context = context
        self.on_event = on_event
        self.output_limit = output_limit
        self.name = f"CallAgent_{_safe_tool_suffix(spec.name)}"[:64]
        self.description = "\n".join(
            part for part in [
                f"Call subagent {spec.name} to complete a delegated task.",
                spec.description,
                "Use this when the task needs this subagent's role, tools, memory, or specialized prompt.",
                "Input must include task; context and expected_output are optional.",
            ] if part
        )
        self.input_schema = {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "要交给子 Agent 完成的具体任务。",
                },
                "context": {
                    "type": "string",
                    "description": "必要上下文，可为空。",
                },
                "expected_output": {
                    "type": "string",
                    "description": "期望输出格式，可为空。",
                },
            },
            "required": ["task"],
            "additionalProperties": False,
        }

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        return PermissionDecision(
            behavior=PermissionBehavior.PASSTHROUGH,
            message=f"Subagent call requires normal permission evaluation: {self.spec.name}",
            decision_reason="Subagent delegation",
        )

    async def check_read_only(self, tool_input: dict[str, Any]) -> bool:
        return True

    async def __call__(self, **kwargs: Any) -> ToolChunk:
        task = str(kwargs.get("task") or "").strip()
        context_text = str(kwargs.get("context") or "").strip()
        expected_output = str(kwargs.get("expected_output") or "").strip()
        if not task:
            return self._chunk(self._blocked("missing_task", "Subagent task is required."), error=True)

        blocked = self._blocked_reason()
        if blocked:
            return self._chunk(blocked, error=True)

        self.context.call_count += 1
        depth = self.context.current_depth + 1
        started = time.perf_counter()
        self._emit("SUBAGENT_CALL_START", {
            "status": "running",
            "task": task,
            "depth": depth,
            "maxDepth": self.context.max_depth,
            "outputPreview": "",
            "durationMs": 0,
        })

        child_guardrails = restrict_child_guardrails(self.parent_guardrails, self.spec.guardrails)
        child_context = SubagentRuntimeContext(
            root_agent_id=self.context.root_agent_id,
            current_depth=depth,
            max_depth=self.context.max_depth,
            call_stack=[*self.context.call_stack, self.spec.id],
            max_calls=self.context.max_calls,
            call_count=self.context.call_count,
        )
        child_subagents = self.spec.subagents if depth < self.context.max_depth else []
        child_runtime_id = (
            f"subagent:{self.context.root_agent_id}:{self.spec.id}:"
            f"depth-{depth}:calls-{self.context.call_count}:stack-{','.join(child_context.call_stack)}"
        )
        prompt = "\n".join(
            part for part in [
                "你是被主 Agent 委派的子 Agent。请在隔离上下文中独立完成任务。",
                "不要输出“我来分析”“我将开始”等准备性话语；必须直接交付可用结果。",
                f"任务：{task}",
                f"上下文：{context_text}" if context_text else "",
                f"期望输出：{expected_output}" if expected_output else "",
                "请只完成被委派的任务，并返回可被主 Agent 直接使用的完整结果。",
            ] if part
        )

        try:
            from src.agents.factory import get_or_create_agent

            child_agent = get_or_create_agent(
                agent_id=child_runtime_id,
                name=self.spec.name,
                system_prompt=self.spec.system_prompt,
                model_name=self.spec.model_name,
                fallback_names=self.spec.fallback_names,
                thinking_enable=False,
                permission_mode=child_guardrails.permission_profile.to_agentscope_permission_mode(),
                model_options=self.spec.model_options,
                guardrails=child_guardrails,
                mcp_servers=self.spec.mcp_servers,
                knowledge_tools=self.spec.knowledge_tools,
                subagents=child_subagents,
                subagent_context=child_context,
                parent_agent_id=self.spec.id,
                parent_agent_name=self.spec.name,
                on_subagent_event=self.on_event,
            )
            output = ""
            async for event in child_agent.reply_stream(UserMsg(name="user", content=prompt)):
                event_data = event.model_dump(mode="json")
                event_type = event_data.get("type")
                if event_type == "TEXT_BLOCK_DELTA":
                    output += str(event_data.get("delta") or "")
            if _is_incomplete_subagent_output(output, task=task, expected_output=expected_output):
                retry_output = ""
                retry_prompt = "\n".join(
                    part for part in [
                        "上一次回复只是准备性说明，没有完成委派任务。",
                        "现在请直接给出最终交付内容，不要解释你要做什么。",
                        f"任务：{task}",
                        f"上下文：{context_text}" if context_text else "",
                        f"期望输出：{expected_output}" if expected_output else "",
                    ] if part
                )
                async for retry_event in child_agent.reply_stream(UserMsg(name="user", content=retry_prompt)):
                    retry_data = retry_event.model_dump(mode="json")
                    if retry_data.get("type") == "TEXT_BLOCK_DELTA":
                        retry_output += str(retry_data.get("delta") or "")
                if len(retry_output.strip()) > len(output.strip()):
                    output = retry_output
            duration_ms = int((time.perf_counter() - started) * 1000)
            self.context.call_count = child_context.call_count
            result = {
                "agentId": self.spec.id,
                "agentName": self.spec.name,
                "status": "completed",
                "output": trim_tool_text(output, self.output_limit),
                "durationMs": duration_ms,
                "error": None,
            }
            self._emit("SUBAGENT_CALL_END", {
                "status": "completed",
                "task": task,
                "depth": depth,
                "maxDepth": self.context.max_depth,
                "durationMs": duration_ms,
                "outputPreview": trim_tool_text(output, 1200),
            })
            return self._chunk(result)
        except Exception as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            result = {
                "agentId": self.spec.id,
                "agentName": self.spec.name,
                "status": "failed",
                "output": "",
                "durationMs": duration_ms,
                "error": str(exc),
            }
            self._emit("SUBAGENT_CALL_FAILED", {
                "status": "failed",
                "task": task,
                "depth": depth,
                "maxDepth": self.context.max_depth,
                "durationMs": duration_ms,
                "outputPreview": "",
                "error": str(exc),
            })
            return self._chunk(result, error=True)

    def _blocked_reason(self) -> dict[str, Any] | None:
        if self.context.call_count >= self.context.max_calls:
            return self._blocked("max_calls", f"Subagent call limit reached: {self.context.max_calls}.")
        if self.context.current_depth >= self.context.max_depth:
            return self._blocked("max_depth", f"Subagent max depth reached: {self.context.max_depth}.")
        if self.spec.id in self.context.call_stack:
            return self._blocked("cycle_detected", f"Subagent cycle detected: {self.spec.name}.")
        return None

    def _blocked(self, reason: str, message: str) -> dict[str, Any]:
        payload = {
            "agentId": self.spec.id,
            "agentName": self.spec.name,
            "status": "blocked",
            "output": "",
            "durationMs": 0,
            "error": message,
            "reason": reason,
        }
        self._emit("SUBAGENT_CALL_FAILED", {
            "status": "blocked",
            "task": "",
            "depth": self.context.current_depth + 1,
            "maxDepth": self.context.max_depth,
            "durationMs": 0,
            "outputPreview": "",
            "error": message,
            "reason": reason,
        })
        return payload

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if not self.on_event:
            return
        try:
            self.on_event({
                "eventType": event_type,
                "parentAgentId": self.parent_agent_id,
                "parentAgentName": self.parent_agent_name,
                "subagentId": self.spec.id,
                "subagentName": self.spec.name,
                **payload,
            })
        except Exception:
            pass

    def _chunk(self, result: dict[str, Any], error: bool = False) -> ToolChunk:
        return ToolChunk(
            content=[TextBlock(text=json.dumps(result, ensure_ascii=False, indent=2))],
            state=ToolResultState.ERROR if error else ToolResultState.SUCCESS,
            is_last=True,
        )
