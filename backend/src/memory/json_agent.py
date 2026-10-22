"""结构化 JSON 系统 Agent。

习惯判断、专业区判断和旧记忆提炼都走这里。这里不写记忆文件。
"""

from __future__ import annotations

import json
import re
from hashlib import sha256
from typing import Any

from agentscope.message import UserMsg
from agentscope.permission import PermissionMode

from src.agents.runtime_types import AgentGuardrails, AgentModelOptions, RuntimeToolScope


async def run_json_agent(
    *,
    agent_id: str,
    name: str,
    system_prompt: str,
    user_prompt: str,
    model_name: str,
    fallback_names: list[str],
    max_tokens: int,
    extra_tools: list[Any] | None = None,
) -> str:
    """跑一个只返回文本的系统 Agent。调用方自己解析 JSON。"""

    from src.agents.factory import get_or_create_agent

    agent = get_or_create_agent(
        agent_id=agent_id,
        name=name,
        system_prompt=system_prompt,
        model_name=model_name,
        fallback_names=fallback_names,
        thinking_enable=False,
        permission_mode=PermissionMode.BYPASS,
        model_options=AgentModelOptions(max_tokens=max_tokens, max_react_iters=2, temperature=0.1),
        guardrails=AgentGuardrails(
            network_enabled=False,
            operation_scope="read_only",
        ),
        tool_scope=RuntimeToolScope(
            allow_mcp=False,
            allow_knowledge=False,
            allow_bash=False,
            allow_http=False,
            allow_web=False,
        ),
        include_default_tools=False,
        extra_tools=extra_tools or [],
    )
    text = ""
    async for event in agent.reply_stream(UserMsg(name="user", content=user_prompt)):
        event_data = event.model_dump(mode="json")
        if event_data.get("type") == "TEXT_BLOCK_DELTA":
            text += str(event_data.get("delta") or "")
    return text


def system_agent_id(kind: str, model_name: str, fallback_names: list[str], prompt_version: str) -> str:
    """按提示词版本和模型生成稳定的系统 Agent id。"""

    fallback_key = ",".join(fallback_names) if fallback_names else "none"
    raw = f"{kind}:{model_name}:fallbacks-{fallback_key}:prompt-{prompt_version}"
    return f"system:{kind}:{sha256(raw.encode('utf-8')).hexdigest()[:16]}"


def extract_json_object(text: str) -> dict[str, Any]:
    """从模型文本里取出一个 JSON 对象。失败时返回空字典。"""

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
