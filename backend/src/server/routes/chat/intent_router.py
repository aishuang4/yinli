"""Chat 架构意图识别与机械执行主体选择。

ReadinessAgent 在本模块前判断信息是否足够；本模块只负责：
- 模型/规则识别 architecture：当前只在线 ReAct，Plan/SubAgent 后续重建。
- 后端按前端显式执行模式机械选择 executor：single_agent 或 team。
"""

import json
import re
from typing import Any, Literal

from agentscope.message import UserMsg
from agentscope.permission import PermissionMode
from pydantic import BaseModel, Field

from src.agents.factory import AgentGuardrails, AgentModelOptions, RuntimeToolScope, get_or_create_agent
from src.server.routes.chat.models import ChatImagePayload


RuntimeKind = Literal["react"]
ExecutorKind = Literal["single_agent", "team"]


class RuntimeRoute(BaseModel):
    """第一层架构模式选择，后续可通过 config 扩展 Plan/SubAgent 等线路。"""

    kind: RuntimeKind = "react"
    confidence: float = 1.0
    reason: str = ""
    config: dict[str, Any] = Field(default_factory=dict)


class ExecutorRoute(BaseModel):
    """第二层机械执行主体，由前端选择决定。"""

    kind: ExecutorKind = "single_agent"
    reason: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


class RouteDecision(BaseModel):
    """单项路由决策及原因。"""

    name: str
    enabled: bool
    confidence: float = 1.0
    reason: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


class IntentRoute(BaseModel):
    """本轮 Chat 运行的前置路由结果。"""

    mode: RuntimeKind = "react"
    runtime: RuntimeRoute = Field(default_factory=RuntimeRoute)
    executor: ExecutorRoute = Field(default_factory=ExecutorRoute)
    decisions: list[RouteDecision] = Field(default_factory=list)
    main_agent_instruction: str = Field(default="", alias="mainAgentInstruction")

    model_config = {"populate_by_name": True}


class ModelIntentRoutePayload(BaseModel):
    """模型第一层路由返回的结构化结果。"""

    runtime: RuntimeRoute = Field(default_factory=RuntimeRoute)
    intent: str = "react"
    confidence: float = 0.5
    reason: str = ""

    model_config = {"extra": "ignore"}


INTENT_CATALOG: list[dict[str, Any]] = [
    {
        "name": "react",
        "description": "默认 ReAct 架构，适合普通问答、工具调用、Skills 执行和执行型任务。",
        "runtimeKind": "react",
    },
]


async def route_chat_message_with_model(
    *,
    message: str,
    agent_config: object | None,
    execution_mode: Literal["agent", "team"],
    images: list[ChatImagePayload],
    model_name: str,
    fallback_names: list[str] | None = None,
) -> IntentRoute:
    """模型优先的 Chat 前置路由，失败时回退到规则路由。

    模型只负责 architecture 选择；最终执行主体、工具、Skill、Team Member 由下层处理。
    """

    fallback_route = route_chat_message(
        execution_mode=execution_mode,
    )
    model_payload = await _run_model_intent_router(
        message=message,
        agent_config=agent_config,
        images=images,
        model_name=model_name,
        fallback_names=fallback_names or [],
    )
    if model_payload is None:
        return fallback_route

    return _sanitize_model_route(
        payload=model_payload,
        fallback_route=fallback_route,
        execution_mode=execution_mode,
    )


def route_chat_message(
    *,
    execution_mode: Literal["agent", "team"],
) -> IntentRoute:
    """生成本轮 Chat 的前置路由结果。"""

    decisions: list[RouteDecision] = []
    runtime = _route_runtime()
    executor = _select_executor(execution_mode)
    decisions.append(RouteDecision(
        name="architecture_route",
        enabled=True,
        confidence=runtime.confidence,
        reason=runtime.reason,
        payload=runtime.model_dump(),
    ))
    decisions.append(RouteDecision(
        name="executor_route",
        enabled=True,
        confidence=1.0,
        reason=executor.reason,
        payload=executor.model_dump(),
    ))
    return IntentRoute(
        mode=runtime.kind,
        runtime=runtime,
        executor=executor,
        decisions=decisions,
        mainAgentInstruction=_main_agent_instruction(runtime.kind, executor.kind),
    )


def route_trace_payload(route: IntentRoute) -> dict[str, Any]:
    """生成可写入 run_events 的稳定 payload。"""

    data = route.model_dump(by_alias=True)
    data["decisions"] = [decision.model_dump() for decision in route.decisions]
    return data


async def _run_model_intent_router(
    *,
    message: str,
    agent_config: object | None,
    images: list[ChatImagePayload],
    model_name: str,
    fallback_names: list[str],
) -> ModelIntentRoutePayload | None:
    """调用轻量模型完成本轮意图识别。"""

    try:
        router_agent_id = _intent_router_agent_id(model_name, fallback_names)
        agent = get_or_create_agent(
            agent_id=router_agent_id,
            name="IntentRouter",
            system_prompt=_build_model_router_system_prompt(),
            model_name=model_name,
            fallback_names=fallback_names,
            thinking_enable=False,
            permission_mode=PermissionMode.BYPASS,
            model_options=AgentModelOptions(max_tokens=1400, max_react_iters=2),
            guardrails=AgentGuardrails(
                network_enabled=False,
                tool_permission_profile="read_only",
            ),
            tool_scope=RuntimeToolScope(
                allow_mcp=False,
                allow_knowledge=False,
                allow_bash=False,
                allow_http=False,
                allow_web=False,
            ),
            include_default_tools=False,
        )
        text = ""
        async for event in agent.reply_stream(UserMsg(name="user", content=_build_model_router_user_prompt(
            message=message,
            agent_config=agent_config,
            images=images,
        ))):
            event_data = event.model_dump(mode="json")
            if event_data.get("type") == "TEXT_BLOCK_DELTA":
                text += str(event_data.get("delta") or "")
        data = _extract_json_object(text)
        if not data:
            return None
        return ModelIntentRoutePayload.model_validate(data)
    except Exception:
        return None


def _intent_router_agent_id(model_name: str, fallback_names: list[str]) -> str:
    """生成固定的系统意图识别 Agent 缓存键。"""

    fallback_key = ",".join(fallback_names) if fallback_names else "none"
    return f"system:intent-router:{model_name}:fallbacks-{fallback_key}"


def _build_model_router_system_prompt() -> str:
    """构建模型意图路由器系统提示词。"""

    return "\n".join([
        "You are the intent router for an Agent workstation.",
        "Return only one compact valid JSON object. Do not call tools. Do not write markdown.",
        "Your only job is to select the first-layer architecture route.",
        "Current supported architecture kinds are: react.",
        "Readiness and clarification were already handled before you. Never ask questions here.",
        "Do not select executor, tools, skills, team members, or permissions.",
        "Team execution is selected mechanically by the application; never return team as runtime.kind.",
        "Prefer runtime.kind=react unless a future supported architecture is explicitly available in the catalog.",
        "Schema:",
        json.dumps(
            {
                "runtime": {
                    "kind": "react",
                    "confidence": 0.0,
                    "reason": "short Chinese reason",
                    "config": {},
                },
                "intent": "react",
                "confidence": 0.0,
                "reason": "short Chinese reason",
            },
            ensure_ascii=False,
        ),
    ])


def _build_model_router_user_prompt(
    *,
    message: str,
    agent_config: object | None,
    images: list[ChatImagePayload],
) -> str:
    """构建模型路由器用户提示词。"""

    agent_summary = {
        "id": str(getattr(agent_config, "id", "") or ""),
        "name": str(getattr(agent_config, "name", "") or ""),
        "description": str(getattr(agent_config, "description", "") or ""),
    }
    payload = {
        "intentCatalog": INTENT_CATALOG,
        "userMessage": message,
        "agent": agent_summary,
        "hasImages": bool(images),
        "routingPolicy": {
            "supportedRuntimeKinds": ["react"],
            "readinessWasHandledBeforeRouter": True,
            "executorIsSelectedAfterRouter": True,
            "doNotUseSkillOrTeamInformation": True,
        },
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _sanitize_model_route(
    *,
    payload: ModelIntentRoutePayload,
    fallback_route: IntentRoute,
    execution_mode: Literal["agent", "team"],
) -> IntentRoute:
    """后端裁剪模型第一层路由，确保只进入当前支持的 runtime。"""

    runtime = _sanitize_runtime_route(
        requested=payload.runtime,
        fallback=fallback_route.runtime,
        payload_confidence=payload.confidence,
        payload_reason=payload.reason,
    )
    executor = _select_executor(execution_mode)
    decisions = [
        RouteDecision(
            name="architecture_route",
            enabled=True,
            confidence=max(0.0, min(float(payload.confidence), 1.0)),
            reason=runtime.reason or payload.reason or "模型完成第一层运行线路识别。",
            payload={
                "intent": payload.intent,
                "requestedRuntime": payload.runtime.model_dump(),
                "sanitizedRuntime": runtime.model_dump(),
            },
        ),
        RouteDecision(
            name="executor_route",
            enabled=True,
            confidence=1.0,
            reason=executor.reason,
            payload=executor.model_dump(),
        ),
    ]
    return IntentRoute(
        mode=runtime.kind,
        runtime=runtime,
        executor=executor,
        decisions=decisions,
        mainAgentInstruction=_main_agent_instruction(runtime.kind, executor.kind),
    )


def _sanitize_runtime_route(
    *,
    requested: RuntimeRoute,
    fallback: RuntimeRoute,
    payload_confidence: float,
    payload_reason: str,
) -> RuntimeRoute:
    """把模型返回的 runtime route 裁剪成当前支持的第一层线路。"""

    if requested.kind not in {"react"}:
        return fallback
    return RuntimeRoute(
        kind=requested.kind,
        confidence=max(0.0, min(float(requested.confidence or payload_confidence), 1.0)),
        reason=requested.reason or payload_reason or "进入 ReAct Runtime。",
        config=requested.config,
    )


def _extract_json_object(text: str) -> dict[str, Any]:
    """从模型输出中提取第一个 JSON 对象。"""

    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(raw[start : end + 1])
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}


def _route_runtime() -> RuntimeRoute:
    """规则兜底的第一层架构模式。"""

    return RuntimeRoute(
        kind="react",
        confidence=0.78,
        reason="进入默认 ReAct Runtime。",
    )


def _select_executor(execution_mode: Literal["agent", "team"]) -> ExecutorRoute:
    """根据前端显式执行模式机械选择执行主体。"""

    if execution_mode == "team":
        return ExecutorRoute(
            kind="team",
            reason="前端选择了 Team 模式，所选 Agent 作为本轮 Leader。",
            payload={"executionMode": "team"},
        )
    return ExecutorRoute(
        kind="single_agent",
        reason="前端选择了单 Agent，上层架构模式下使用单 Agent 执行主体。",
        payload={"executionMode": "agent"},
    )


def _main_agent_instruction(mode: RuntimeKind, executor: ExecutorKind = "single_agent") -> str:
    executor_text = "执行主体为 Team，Leader 动态组建 Member 并基于共享成果综合。" if executor == "team" else "执行主体为单 Agent。"
    if mode == "react":
        return f"本轮按 ReAct 架构处理；不要主动输出计划或调用子 Agent。{executor_text}"
    return ""
