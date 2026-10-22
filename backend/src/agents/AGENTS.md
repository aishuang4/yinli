# agents

`backend/src/agents/` 里的 AgentScope 构造。包：[../../AGENTS.md](../../AGENTS.md)。根：[../../../AGENTS.md](../../../AGENTS.md)。

`factory.py` 是唯一的构造入口。新字段写到拥有它的那个文件。`runtime_types.py` 只做转发。

## 文件

- `factory.py` — 为这一轮选定模型、工具集、工作目录和上下文配置。
- `model_factory.py` — 创建一个供应商模型，以及降级链。发出 `attempt`、`success`、`failure`。
- `model_options.py` — 采样参数和 `MAX_REACT_ITERS`（512）。profile 的 `max_react_iters` 默认 128。
- `openai_message_formatter.py` — 发给 OpenAI 兼容接口和 Moonshot 之前，去掉非 user 消息上的 `name`。
- `context_config.py` — 压缩触发 0.75、保留 0.2、摘要五字段、上海时区时间提示。
- `toolkit_builder.py` — Read、Grep、Glob、AskUser，以及 `RuntimeToolScope` 允许的工具。
- `tool_specs.py` — 这一轮的 MCP、知识库工具和工具开关。
- `guardrails.py` — 文件范围、shell 黑名单、网络、Docker、超时和权限档。
- `workspace_paths.py` — `normalize_filesystem_scope`。空范围和文件系统根退回默认 Project 目录。把 profile 复制进持久沙箱。
- `agent_cache.py` — 最多 48 个实例，闲置 6 小时淘汰。正在跑或等待用户的实例留下。
- `session_context.py` — 同一会话换实例时搬走 `state.context`。进程重启后从 `chat_messages` 重建。
- `permission_adapter.py` — 工具执行前后的平台授权。框架自己记住的允许规则不算授权。
- `permission_review_model.py` — 一次审核调用。不走缓存，不带工具、历史、降级和重试。
- `guarded_bash.py` — shell 黑名单和超时。macOS 与 Linux 用 POSIX sh。Windows 用 `cmd.exe`。
- `agentscope_usage_middleware.py` — 把供应商 prompt cache 用量补到模型调用结束事件。
- `system_agents.py` — 内置 id：`default` 和 `system-websearch-job`。

## 规则

- 缓存键不含 system prompt 正文。复用实例时刷新 `_system_prompt`。
- profile 或工具配置变更后调用 `invalidate_agent()`。
- `context_size` 读 `ModelEntry.contextSize`。`0` 表示适配器默认。不退回 32768。
- `include_default_tools=False` 创建没有工具的 Agent。
- `WebFetch` 读取一个已知的 HTTP(S) URL。拒绝链路本地和云元数据、本机 API 端口、URL 凭据、超大响应和非文本内容。
- 托管调用前用 `src/config/model_catalog.py` 的 `assert_model_urls_public` 检查模型地址。
- 本机工作目录默认是项目根。Docker 用 `DockerWorkspace`。

文件目录见 [../catalog/AGENTS.md](../catalog/AGENTS.md)。
