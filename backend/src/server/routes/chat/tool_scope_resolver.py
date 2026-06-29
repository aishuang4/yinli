"""ReAct Runtime 的工具范围解析器。

工具注入范围由 Runtime 上下文决定，不属于前置意图路由结果。
"""

import re
from dataclasses import dataclass

from src.agents.factory import RuntimeToolScope


@dataclass(frozen=True)
class ToolScopeDecision:
    """本轮工具范围及其说明。"""

    scope: RuntimeToolScope
    reason: str = ""


def resolve_runtime_tool_scope(
    *,
    message: str,
    agent_config: object | None,
    has_selected_skills: bool,
    has_team: bool,
    has_images: bool,
) -> ToolScopeDecision:
    """根据消息、Agent 配置和执行上下文解析本轮工具范围。"""

    text = message.strip().lower()
    configured_mcp = bool(list(getattr(agent_config, "mcp_tools", []) or []))
    configured_knowledge = bool(list(getattr(agent_config, "knowledge_tools", []) or []))
    needs_network = bool(re.search(r"(http|https|接口|api|网页|网站|联网|搜索|调研|资料|下载|请求|curl|url|链接|最新|今天|新闻|价格|汇率)", text))
    needs_shell = bool(re.search(r"(运行|执行|测试|构建|启动|命令|脚本|pytest|npm|uv|bash|shell|终端|部署)", text))
    needs_knowledge = bool(re.search(r"(知识库|资料库|文档库|检索|搜索|查询|调研|资料|上下文|文档)", text))
    needs_mcp = bool(re.search(r"(mcp|外部工具|数据库|浏览器|github|figma|notion|jira|linear|slack)", text))

    allow_knowledge = configured_knowledge and (needs_knowledge or needs_network or has_selected_skills or has_team)
    allow_mcp = configured_mcp and (needs_mcp or needs_network or has_team)
    allow_bash = needs_shell
    allow_http = needs_network

    reasons: list[str] = []
    if allow_knowledge:
        reasons.append("匹配到知识库/资料检索意图")
    if allow_mcp:
        reasons.append("匹配到 MCP/外部工具意图")
    if allow_bash:
        reasons.append("匹配到命令执行意图")
    if allow_http:
        reasons.append("匹配到 HTTP/网络请求意图")
    if has_images and not reasons:
        reasons.append("图片任务保留基础多模态处理，不额外启用网络工具")

    return ToolScopeDecision(
        scope=RuntimeToolScope(
            allow_mcp=allow_mcp,
            allow_knowledge=allow_knowledge,
            allow_bash=allow_bash,
            allow_http=allow_http,
        ),
        reason="；".join(reasons),
    )
