"""AgentScope 上下文压缩与运行时提示注入。

工厂创建 Agent 时取这里的配置。压缩字段、触发比例和时区不放进工厂。
"""

from agentscope.agent._config import ContextConfig, InjectionConfig


# 压缩摘要各字段上限（字符）。jsonschema 会严格校验 maxLength，模型超长会让压缩失败并中断运行，
# 因此上限只能放宽不能收紧；靠 compression_prompt 引导精炼。
CONTEXT_SUMMARY_FIELD_LIMIT = 2000
CONTEXT_SUMMARY_NEXT_STEPS_LIMIT = 1200
# 何时压缩 / 压缩后保留多少：窗口的 75% 触发，保留最近约 20% 的原文。
# 触发点比 AgentScope 默认 0.8 略早，是给 InjectionConfig 提前量 + 一轮工具输出留余量；
# 保留比例比默认 0.1 高，避免刚压完就丢掉正在处理的文件内容。
CONTEXT_COMPRESSION_TRIGGER_RATIO = 0.75
CONTEXT_COMPRESSION_RESERVE_RATIO = 0.2
# 单条 tool result 进入上下文的 token 上限；超过部分由 AgentScope 截断并写入 workspace。
CONTEXT_TOOL_RESULT_LIMIT = 4000
RUNTIME_STATE_TIMEZONE = "Asia/Shanghai"


def _build_context_config() -> ContextConfig:
    """创建 AgentScope 上下文压缩配置。

    只声明 `summary_template` 会消费的五个字段；此前的 `title` / `summary` 兼容字段
    从未被模板渲染，只会让模型多花输出 tokens，改为用 additionalProperties 容忍。
    """

    summary_schema = {
        "title": "SummarySchema",
        "description": "用于生成长上下文压缩摘要的结构化模型。",
        "type": "object",
        "properties": {
            "task_overview": {
                "title": "Task Overview",
                "type": "string",
                "maxLength": CONTEXT_SUMMARY_FIELD_LIMIT,
                "description": "用户核心请求、成功标准和明确约束。",
            },
            "current_state": {
                "title": "Current State",
                "type": "string",
                "maxLength": CONTEXT_SUMMARY_FIELD_LIMIT,
                "description": "已完成事项、修改过的文件路径和关键产物。",
            },
            "important_discoveries": {
                "title": "Important Discoveries",
                "type": "string",
                "maxLength": CONTEXT_SUMMARY_FIELD_LIMIT,
                "description": "关键技术发现、约束、决策原因、错误和解决方式。",
            },
            "next_steps": {
                "title": "Next Steps",
                "type": "string",
                "maxLength": CONTEXT_SUMMARY_NEXT_STEPS_LIMIT,
                "description": "下一步行动、阻塞点和优先级。",
            },
            "context_to_preserve": {
                "title": "Context To Preserve",
                "type": "string",
                "maxLength": CONTEXT_SUMMARY_FIELD_LIMIT,
                "description": "需要保留的用户偏好、项目约定、已读过的 Skill 名称和上下文。",
            },
        },
        # 不写 required、允许额外字段：模型偶尔多返回 title / summary 之类字段时不能让整轮运行失败；
        # 缺字段会让 AgentScope 的 summary_template 渲染报错，所以由 compression_prompt 明确要求五段都填。
        "additionalProperties": True,
    }
    return ContextConfig(
        trigger_ratio=CONTEXT_COMPRESSION_TRIGGER_RATIO,
        reserve_ratio=CONTEXT_COMPRESSION_RESERVE_RATIO,
        compression_prompt=(
            "<system-hint>You are compressing the previous conversation so the "
            "agent can continue reliably. Keep every field concise, factual, "
            "and under the schema maxLength. Prefer compact Chinese bullets "
            "when the conversation is Chinese. Fill all five fields "
            "task_overview, current_state, important_discoveries, next_steps, "
            "and context_to_preserve. Use absolute file paths, identifiers and "
            "dates. Do not copy long tool outputs verbatim.</system-hint>"
        ),
        summary_schema=summary_schema,
        tool_result_limit=CONTEXT_TOOL_RESULT_LIMIT,
    )


def _build_injection_config() -> InjectionConfig:
    """创建运行时状态注入配置：时间按上海时区、每半小时最多刷新一次。"""

    return InjectionConfig(
        inject_runtime_state=True,
        timezone=RUNTIME_STATE_TIMEZONE,
        # 时区由 AgentScope 另起 <timezone> 字段注入；格式里不能带 %Z，否则 strptime 回读失败会每轮重复注入。
        time_format="%Y-%m-%d %H:%M",
        time_interval=0.5,
        context_buffer_ratio=0.15,
        emit_hint_event=False,
    )
