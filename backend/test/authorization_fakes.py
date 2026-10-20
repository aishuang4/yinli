"""授权契约测试的内存工具与模型，不访问文件、网络或真实凭据。"""

import asyncio
from functools import wraps

from agentscope.agent import Agent
from agentscope.message import AssistantMsg, TextBlock, ToolCallBlock, ToolResultState
from agentscope.middleware import MiddlewareBase
from agentscope.model import ChatModelBase, ChatResponse
from agentscope.permission import PermissionBehavior, PermissionDecision
from agentscope.tool import ToolBase, ToolChunk, Toolkit


class MemoryTool(ToolBase):
    """只记录实际参数，可模拟框架许可结果。"""

    name = "MemoryTool"
    description = "无副作用契约工具"
    input_schema = {"type": "object", "properties": {"value": {"type": "integer"}},
                    "required": ["value"], "additionalProperties": False}
    is_read_only = False
    is_concurrency_safe = True

    def __init__(self, log, behavior=PermissionBehavior.ALLOW, external=False):
        super().__init__()
        self.log = log
        self.behavior = behavior
        self.is_external_tool = external

    async def check_permissions(self, tool_input, permission_context):
        self.log.append(("engine", dict(tool_input)))
        return PermissionDecision(behavior=self.behavior, message="fake")

    async def call(self, **kwargs):
        self.log.append(("call", kwargs))
        return ToolChunk(content=[TextBlock(text="ok")], state=ToolResultState.SUCCESS)


class MemoryModel(ChatModelBase):
    """调用原生 reasoning 时只返回固定内存文本。"""

    def __init__(self):
        super().__init__(credential=None, model="memory-only", parameters=self.Parameters(), max_retries=0)
        self.requests = []

    async def _call_api(self, model, **kwargs):
        self.requests.append(kwargs)
        return ChatResponse(content=[TextBlock(text="native reasoning")], is_last=True)


class ActingRecorder(MiddlewareBase):
    """记录 SDK acting hook 的调用顺序。"""

    def __init__(self, log):
        self.log = log

    async def on_acting(self, agent, input_kwargs, next_handler):
        self.log.append(("acting", input_kwargs["tool_call"].id))
        async for item in next_handler(**input_kwargs):
            yield item


def make_agent(tool, calls, agent_class=Agent, **kwargs):
    """建立真实 SDK Agent，并放入它实际消费的 assistant tool call。"""

    agent = agent_class(name="contract", system_prompt="fake", model=MemoryModel(),
                        toolkit=Toolkit(tools=[tool]), **kwargs)
    agent.state.context.append(AssistantMsg(id=agent.state.reply_id, name=agent.name, content=calls))
    return agent


def make_call(call_id="c", value=1, **kwargs):
    """动作参数只含测试整数。"""

    return ToolCallBlock(id=call_id, name="MemoryTool", input=f'{{"value":{value}}}', **kwargs)


async def collect(stream):
    """完整消费原生生成器，验证结束事件而非仅启动。"""

    return [item async for item in stream]


def run_async(fn):
    """沿用仓库 asyncio.run 风格，不添加测试运行依赖。"""

    @wraps(fn)
    def run(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))
    return run
