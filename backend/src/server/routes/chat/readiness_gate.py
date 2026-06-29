"""Chat 前置信息充足性判断。

本模块只回答一个问题：当前用户输入是否足以启动 Runtime。
它不选择 ReAct/Plan/SubAgent，也不选择 single Agent/Team。
"""

import json
import re
from typing import Any

from agentscope.message import UserMsg
from agentscope.permission import PermissionMode
from pydantic import BaseModel, Field

from src.agents.factory import AgentGuardrails, AgentModelOptions, RuntimeToolScope, get_or_create_agent
from src.server.routes.chat.models import ChatImagePayload, ChatTeamMemberPayload


class ReadinessDecision(BaseModel):
    """前置信息充足性判断结果。"""

    ready: bool = True
    confidence: float = 1.0
    reason: str = ""
    clarification: dict[str, Any] | None = None
    source: str = "rule"


class ModelReadinessPayload(BaseModel):
    """ReadinessAgent 返回的结构化结果。"""

    ready: bool = True
    confidence: float = 0.5
    reason: str = ""
    clarification: dict[str, Any] | None = None

    model_config = {"extra": "ignore"}


async def check_message_readiness_with_model(
    *,
    message: str,
    agent_config: object | None,
    skill_ids: list[str],
    team_members: list[ChatTeamMemberPayload],
    images: list[ChatImagePayload],
    model_name: str,
    fallback_names: list[str] | None = None,
) -> ReadinessDecision:
    """模型优先判断用户输入是否足够开始执行。"""

    fallback = check_message_readiness_by_rule(message)
    if not message.strip():
        return fallback

    model_payload = await _run_readiness_agent(
        message=message,
        agent_config=agent_config,
        skill_ids=skill_ids,
        team_members=team_members,
        images=images,
        model_name=model_name,
        fallback_names=fallback_names or [],
    )
    if model_payload is None:
        return fallback
    return _sanitize_readiness_payload(model_payload, fallback=fallback)


def check_message_readiness_by_rule(message: str) -> ReadinessDecision:
    """规则兜底的信息充足性判断。"""

    clarification = _detect_missing_required_context(message)
    return ReadinessDecision(
        ready=clarification is None,
        confidence=0.75 if clarification else 0.65,
        reason="执行前缺少必要目标信息。" if clarification else "未发现必须前置追问的信息缺口。",
        clarification=clarification,
        source="rule",
    )


async def _run_readiness_agent(
    *,
    message: str,
    agent_config: object | None,
    skill_ids: list[str],
    team_members: list[ChatTeamMemberPayload],
    images: list[ChatImagePayload],
    model_name: str,
    fallback_names: list[str],
) -> ModelReadinessPayload | None:
    """调用专用 ReadinessAgent 完成信息充足性判断。"""

    try:
        agent = get_or_create_agent(
            agent_id=_readiness_agent_id(model_name, fallback_names),
            name="ReadinessAgent",
            system_prompt=_build_readiness_system_prompt(),
            model_name=model_name,
            fallback_names=fallback_names,
            thinking_enable=False,
            permission_mode=PermissionMode.BYPASS,
            model_options=AgentModelOptions(max_tokens=1000, max_react_iters=2),
            guardrails=AgentGuardrails(
                network_enabled=False,
                tool_permission_profile="read_only",
            ),
            tool_scope=RuntimeToolScope(
                allow_mcp=False,
                allow_knowledge=False,
                allow_bash=False,
                allow_http=False,
            ),
            include_default_tools=False,
        )
        text = ""
        async for event in agent.reply_stream(UserMsg(
            name="user",
            content=_build_readiness_user_prompt(
                message=message,
                agent_config=agent_config,
                skill_ids=skill_ids,
                team_members=team_members,
                images=images,
            ),
        )):
            event_data = event.model_dump(mode="json")
            if event_data.get("type") == "TEXT_BLOCK_DELTA":
                text += str(event_data.get("delta") or "")
        data = _extract_json_object(text)
        if not data:
            return None
        return ModelReadinessPayload.model_validate(data)
    except Exception:
        return None


def _readiness_agent_id(model_name: str, fallback_names: list[str]) -> str:
    """生成固定的系统 ReadinessAgent 缓存键。"""

    fallback_key = ",".join(fallback_names) if fallback_names else "none"
    return f"system:readiness-agent:{model_name}:fallbacks-{fallback_key}"


def _build_readiness_system_prompt() -> str:
    """构建 ReadinessAgent 系统提示词。"""

    return "\n".join([
        "You are the readiness gate for an Agent workstation.",
        "Return only one compact valid JSON object. Do not call tools. Do not write markdown.",
        "Your only job is to decide whether the user provided enough information to start execution.",
        "Do not select architecture, executor, tools, skills, workers, or permissions.",
        "Set ready=false only when a critical target, scope, credential, parameter, or user preference is missing and execution would be unreliable.",
        "If ready=false, provide concise clarification questions.",
        "If the user asks a general question or asks for advice, ready should usually be true.",
        "Schema:",
        json.dumps(
            {
                "ready": True,
                "confidence": 0.0,
                "reason": "short Chinese reason",
                "clarification": {
                    "title": "short title",
                    "description": "short description",
                    "questions": [{"id": "target", "type": "text | choice", "label": "question"}],
                },
            },
            ensure_ascii=False,
        ),
    ])


def _build_readiness_user_prompt(
    *,
    message: str,
    agent_config: object | None,
    skill_ids: list[str],
    team_members: list[ChatTeamMemberPayload],
    images: list[ChatImagePayload],
) -> str:
    """构建 ReadinessAgent 用户提示词。"""

    payload = {
        "userMessage": message,
        "agent": {
            "id": str(getattr(agent_config, "id", "") or ""),
            "name": str(getattr(agent_config, "name", "") or ""),
            "description": str(getattr(agent_config, "description", "") or ""),
            "skillCount": len(skill_ids),
            "configuredMcpToolCount": len(list(getattr(agent_config, "mcp_tools", []) or [])),
            "configuredKnowledgeToolCount": len(list(getattr(agent_config, "knowledge_tools", []) or [])),
        },
        "hasImages": bool(images),
        "teamContext": {
            "enabled": bool(team_members),
            "memberCount": len(team_members),
        },
        "policy": {
            "readinessOnly": True,
            "doNotRouteArchitecture": True,
            "doNotSelectExecutor": True,
        },
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _sanitize_readiness_payload(
    payload: ModelReadinessPayload,
    *,
    fallback: ReadinessDecision,
) -> ReadinessDecision:
    """裁剪模型返回的信息充足性结果。"""

    confidence = max(0.0, min(float(payload.confidence), 1.0))
    if payload.ready:
        return ReadinessDecision(
            ready=True,
            confidence=confidence,
            reason=payload.reason or "ReadinessAgent 判断信息足够，可以进入架构路由。",
            clarification=None,
            source="model",
        )

    clarification = _normalize_clarification(payload.clarification)
    if clarification is None:
        clarification = fallback.clarification or {
            "title": "需要补充信息",
            "description": payload.reason or "当前任务需要补充关键信息后再执行。",
            "questions": [{"id": "details", "type": "text", "label": "请补充需要处理的目标、范围或约束"}],
        }
    return ReadinessDecision(
        ready=False,
        confidence=confidence,
        reason=payload.reason or "ReadinessAgent 判断执行前需要补充信息。",
        clarification=clarification,
        source="model",
    )


def _normalize_clarification(value: dict[str, Any] | None) -> dict[str, Any] | None:
    """清理模型返回的追问结构。"""

    if not isinstance(value, dict):
        return None
    questions = value.get("questions")
    if not isinstance(questions, list):
        questions = []
    normalized_questions = []
    for index, item in enumerate(questions[:5]):
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or item.get("question") or "").strip()
        if not label:
            continue
        normalized_questions.append({
            "id": str(item.get("id") or f"question_{index + 1}")[:40],
            "type": str(item.get("type") or "text"),
            "label": label[:120],
            **({"options": item.get("options")} if isinstance(item.get("options"), list) else {}),
        })
    if not normalized_questions:
        return None
    return {
        "title": str(value.get("title") or "需要补充信息")[:80],
        "description": str(value.get("description") or "")[:240],
        "questions": normalized_questions,
    }


def _detect_missing_required_context(message: str) -> dict[str, Any] | None:
    """规则兜底识别明显的信息缺口。"""

    text = message.strip()
    if not text:
        return {
            "title": "需要补充任务内容",
            "description": "当前消息为空，无法判断要执行的任务。",
            "questions": [{"id": "task", "type": "text", "label": "请补充要处理的任务"}],
        }
    if re.search(r"(修改|修复|读取|检查|运行|删除|重命名)", text) and not re.search(r"(/|\\|\.py|\.ts|\.tsx|\.md|文件|目录|项目|代码|接口|页面|组件)", text):
        return {
            "title": "需要补充目标对象",
            "description": "该请求像执行型任务，但缺少明确的文件、目录、项目或对象。",
            "questions": [{"id": "target", "type": "text", "label": "要处理的目标是什么？"}],
        }
    return None


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
