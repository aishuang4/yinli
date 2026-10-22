# catalog

`backend/src/catalog/` 的文件目录。包：[../../AGENTS.md](../../AGENTS.md)。

- 用户 profile 在 `<tenant>/agents/profiles/<id>.md`（`profiles_dir()`）。
- 系统覆盖在 `<tenant>/agents/_system/profiles/`（`system_profiles_dir()`）。
- `get_registered_agent` 读这份目录。不要把用户 profile 正文 upsert 进 sqlite。
- Skill 正文来自 `SKILL.md`。已启用且 `availableToAllAgents`（默认 true）的包进入单 Agent、沙箱和 Team Leader 目录，不必写进 profile。
- 关掉这个开关的 Skill 仍要手动绑定。
- Job Agent、群聊 Worker、群聊 Host、Team Member 和无工具的系统 Agent 不因这个开关拿到 Skill。
- `src/catalog/skills.py` 的 `ALWAYS_INJECTED_SKILL_IDS` 只有 `yinli-catalog`。要增加始终注入的包，先改这个元组。
- MCP 定义在 `<tenant>/mcp`。HTTP 工具定义在 `<tenant>/tools`。密钥用 `secret:` 引用。不要把密钥写进 YAML。
- HTTP 认证配置由调用方调用 `secrets.store_http_auth`。`http_files.py` 只写 YAML。
- Job 定义在 `<tenant>/agents/jobs/<id>.md`。
- `profiles.py` 读写 Agent Markdown。解析时调用 `apply_role_invariants`。
- `role_invariants.py` 规定 `default` 不能改为 job，`system-websearch-job` 不能改为 chat，并校验 Job 工具层。
- `agent_models.py` 是持久 Agent 模型。导入导出请求在 `src/server/routes/agents.py`。

见 [docs/file-catalog-v1.md](../../../docs/file-catalog-v1.md)。
