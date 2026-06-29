"""Chat 主 Agent 执行流程兼容入口。

新代码应使用 runtime_dispatcher.execute_chat_runtime。
本模块仅保留旧 import 路径，避免外部调用方瞬间失效。
"""

from src.server.routes.chat.runtime_dispatcher import execute_chat_runtime


execute_main_agent_flow = execute_chat_runtime
