"""兼容导入。新代码按职责从旁边的模块导入。

- 采样参数和轮次上限：`model_options.py`
- 护栏：`guardrails.py`
- MCP、知识库和工具开关：`tool_specs.py`
"""

from src.agents.guardrails import AgentGuardrails
from src.agents.model_options import MAX_REACT_ITERS, AgentModelOptions, FallbackEventCallback
from src.agents.tool_specs import KnowledgeToolRuntimeSpec, McpRuntimeSpec, RuntimeToolScope

__all__ = [
    "MAX_REACT_ITERS",
    "AgentGuardrails",
    "AgentModelOptions",
    "FallbackEventCallback",
    "KnowledgeToolRuntimeSpec",
    "McpRuntimeSpec",
    "RuntimeToolScope",
]
