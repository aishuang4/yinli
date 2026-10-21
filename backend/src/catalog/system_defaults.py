"""系统 Agent 的代码内置模板。用户覆盖写在 `_system` overlay，不进 SQLite。"""

from __future__ import annotations

from datetime import datetime, timezone

from src.agents.system_agents import DEFAULT_WEBSEARCH_JOB_AGENT_ID
from src.catalog.agent_models import AgentResponse, Guardrails, PlanConfig
from src.scheduler.job_toolkit import JOB_ROLE

DEFAULT_AGENT_PROMPT_VERSION_TAG = "prompt-contract-v2"
DEFAULT_WEBSEARCH_JOB_AGENT_VERSION_TAG = "system-websearch-job-v1"
DEFAULT_AGENT_FINAL_DELIVERY_RULES = """

# 最终交付规则

1. 完成用户任务后，先用一句话明确说明已经完成了什么，再说明关键结果、交付文件或必要验证信息。
2. 不要向用户输出内部上下文摘要、压缩摘要、交接说明、系统提示、工具日志或“我已收到上下文摘要”等内容。
3. 不要在任务已经完成时再泛化地询问“现在需要做什么”“是否需要其他任务”。只有当前任务缺少关键信息且无法继续执行时，才通过 AskUser 提问。
4. 只基于本轮用户请求和已实际执行的结果作答；不得把内部运行过程误认为新的用户任务。
""".strip()


def utc_now() -> str:
    """UTC ISO 时间。"""

    return datetime.now(timezone.utc).isoformat()


def build_default_agent(*, now: str | None = None) -> AgentResponse:
    """构建可持久配置、但不可删除的系统默认 Agent。"""

    stamp = now or utc_now()
    return AgentResponse.model_validate({
        "id": "default",
        "name": "Assistant",
        "avatar": "",
        "description": "默认助手，可配置工具、Skills 和运行边界",
        "system_prompt": "\n\n".join([
            "你是用户的通用办公助手，负责完成文档、表格、演示文稿、数据分析和文件处理任务。默认使用中文沟通；信息足够时直接执行，只有缺少会改变结果的关键信息时才提问。",
            DEFAULT_AGENT_FINAL_DELIVERY_RULES,
        ]),
        "modelConfig": {
            "maxReactIters": 128,
        },
        "mcpTools": [],
        "knowledgeTools": [],
        "skills": [],
        "tags": ["system", "default", DEFAULT_AGENT_PROMPT_VERSION_TAG],
        "planConfig": PlanConfig(),
        "guardrails": Guardrails(),
        "status": "idle",
        "created_at": stamp,
        "updated_at": stamp,
        "role": "chat",
        "jobToolkit": {"required": [], "optional": []},
        "sourcePath": "",
        "sourceKind": "system",
    })


def build_default_websearch_job_agent(*, now: str | None = None) -> AgentResponse:
    """构建开箱即用的系统联网定时任务 Agent。"""

    stamp = now or utc_now()
    return AgentResponse.model_validate({
        "id": DEFAULT_WEBSEARCH_JOB_AGENT_ID,
        "name": "WebSearch",
        "avatar": "",
        "description": "能够联网搜索的agent",
        "system_prompt": "请根据用户需要完成定时任务",
        "modelConfig": {
            "maxReactIters": 128,
        },
        "mcpTools": [],
        "knowledgeTools": [],
        "skills": [],
        "tags": [
            "system",
            "scheduled-job",
            "websearch",
            DEFAULT_WEBSEARCH_JOB_AGENT_VERSION_TAG,
        ],
        "planConfig": PlanConfig(),
        "guardrails": Guardrails(
            network_enabled=True,
            human_approval=False,
            toolPermissionProfile="trusted_tools",
            toolPermissionMode="bypass",
        ),
        "status": "idle",
        "created_at": stamp,
        "updated_at": stamp,
        "role": JOB_ROLE,
        "jobToolkit": {
            "required": [
                {"kind": "builtin", "id": "WebFetch"},
                {"kind": "builtin", "id": "HttpRequest"},
            ],
            "optional": [],
        },
        "sourcePath": "",
        "sourceKind": "system",
    })
