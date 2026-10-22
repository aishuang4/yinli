"""创建模型时传入的采样参数，以及 ReAct 轮次上限。"""

from dataclasses import dataclass
from typing import Any, Callable


# 配置里的 max_react_iters 不能超过这个值。工厂创建主 Agent 时也用它作为循环上限。
MAX_REACT_ITERS = 512
FallbackEventCallback = Callable[[dict[str, Any]], None]


@dataclass(frozen=True)
class AgentModelOptions:
    """本轮模型采样参数。max_react_iters 默认 128，由调用方按场景覆盖。"""

    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    max_react_iters: int = 128
