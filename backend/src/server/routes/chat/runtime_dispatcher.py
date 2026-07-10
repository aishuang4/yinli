"""Chat Runtime 分发器。

统一 Chat 入口只在这里根据第一层 architecture 选择具体 Runtime。
第二层 single_agent/team 是 runtime 内部的机械 executor，不属于意图模型职责。
"""

from typing import AsyncGenerator

from src.server.routes.chat.completion import emit_failed_startup_run
from src.server.routes.chat.models import ChatMessage
from src.server.routes.chat.react_runtime import execute_react_runtime
from src.server.routes.chat.run_context import ChatRunContext


async def execute_chat_runtime(
    *,
    ctx: ChatRunContext,
    run_data: ChatMessage,
) -> AsyncGenerator[str, None]:
    """按 architecture 路由执行对应 Runtime。"""

    if ctx.intent_route.runtime.kind == "react":
        async for event in execute_react_runtime(ctx=ctx, run_data=run_data):
            yield event
        return

    async for event in emit_failed_startup_run(
        session_id=ctx.session_id,
        persist=ctx.persist_run,
        agent_id=ctx.agent_id,
        agent_name=ctx.agent_name,
        model_name=ctx.model_name,
        assistant_error=f"暂不支持的运行线路: {ctx.intent_route.runtime.kind}",
        skill_runs=ctx.skill_runs,
        sandbox=run_data.sandbox,
    ):
        yield event
