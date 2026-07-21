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
    needs_http = bool(re.search(
        r"(接口|\bapi\b|\brest\b|webhook|http请求|网络请求|\bcurl\b|\b(get|post|put|patch|delete|head)\b)",
        text,
    ))
    needs_web = bool(re.search(
        r"(https?://|网页|网站|页面|链接|\burl\b|抓取|读取网页|提取正文|联网|搜索|调研|资料|下载|最新|今天|新闻|价格|汇率)",
        text,
    ))
    needs_network = needs_http or needs_web
    needs_knowledge = bool(re.search(r"(知识库|资料库|文档库|检索|搜索|查询|调研|资料|上下文|文档)", text))
    needs_mcp = bool(re.search(r"(mcp|外部工具|数据库|浏览器|github|figma|notion|jira|linear|slack)", text))

    allow_knowledge = configured_knowledge and (needs_knowledge or needs_network or has_selected_skills or has_team)
    allow_mcp = configured_mcp and (needs_mcp or needs_network or has_team)
    # Bash 是 Skill 执行 scripts/ 下工具的通用执行面。Skill 的脚本需求通常
    # 不会出现在用户原话中，因此不能用关键词裁剪；实际执行仍经过权限策略、
    # 黑名单与工作目录限制。
    allow_bash = True
    allow_http = needs_http
    allow_web = needs_web

    reasons: list[str] = []
    if allow_knowledge:
        reasons.append("匹配到知识库/资料检索意图")
    if allow_mcp:
        reasons.append("匹配到 MCP/外部工具意图")
    if allow_bash:
        reasons.append("保留通用 Shell 以支持 Skill 脚本与工作区操作")
    if allow_http:
        reasons.append("匹配到 HTTP/网络请求意图")
    if allow_web:
        reasons.append("匹配到公开网页读取意图")
    if has_images and not reasons:
        reasons.append("图片任务保留基础多模态处理，不额外启用网络工具")

    return ToolScopeDecision(
        scope=RuntimeToolScope(
            allow_mcp=allow_mcp,
            allow_knowledge=allow_knowledge,
            allow_bash=allow_bash,
            allow_http=allow_http,
            allow_web=allow_web,
        ),
        reason="；".join(reasons),
    )
