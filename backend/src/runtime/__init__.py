"""运行时公开入口。

产品域之间不要互相反向 import 内部模块。Chat、群聊和定时任务共用的规格在：

- agent_spec：主 Agent 规格、缓存 key、按规格创建
- governance：上下文 middleware、记忆注入、会话状态
- chat_session：查找和确保 Chat session
- agent_resources：MCP、知识库工具和文件范围
- scheduled_chat：定时任务启动、读取、取消和对账 Chat Run
- agent_python：Agent Bash 使用的隔离 Python 环境
"""
