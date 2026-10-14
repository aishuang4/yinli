# 实施记录

> 随实施进度更新；PR 描述从这里整理。托管数据已清空，不做迁移（任务 0.1 由仓库负责人在部署时执行）。

## 任务 0.3「实施前核对」结论

- **model_catalog 空目录（§9.1-8）**：接受。`defaultModelId=""` 时不做存在性校验，`providers` / `models` 可为空；新租户可以播种空目录。
- **AgentScope Docker 客户端（§12.4.2）**：AgentScope 2.0.5 的 `DockerWorkspace` 懒加载 `aiodocker`（可选依赖，本 change 已加入 pyproject）。`aiodocker.Docker()` 无参时按 Docker context → `DOCKER_HOST` → 本机 sock 顺序解析；TLS（方案 B）不读 `DOCKER_CERT_PATH`，需要在 `HostedDockerWorkspace` 子类覆盖 `_ensure_client`，显式传 `ssl_context`。
- **skill_paths 挂载方式（§12.4.2）**：不是 bind mount。首次初始化经 `add_skill()` 把本地目录复制进容器 `skills/`（docker backend 用 put_archive）；`skills/` 非空则跳过。无需 daemon 路径转换；宿主后续改动不会同步进已存在容器。
- **workspace backend 单次命令（§12.4.5）**：可以。docker backend 有 `exec_shell(argv, cwd=, timeout=) -> ExecResult(exit_code, stdout, stderr)`，script 定时任务直接复用 workspace，不需要一次性容器。
- **模型 SDK 注入 httpx client（§11.4）**：可以。AgentScope 模型类把 `client_kwargs` 透传给 SDK，OpenAI / DeepSeek / Anthropic 都接受 `http_client`。
- **`mcp/registry.py:437` 线程回调（§10.7）**：只做 HTTP GET 与 JSON 规范化，不读写租户数据，不需要 copy_context。
- **key2api tools / stream_options（§11.6）**：外部 OpenAI 兼容网关，本仓库代码无法确认；按 design 兜底（不支持就去掉 `stream_options`，usage 记 0），联调时验证。
- **桌面会话 TTL（§11.6）**：现状不满足。`SESSION_TTL=14 天` 对所有 client_kind 一刀切，`touch_session` 只更新 `last_seen_at` 不滑动续期；§11.6 需要把 desktop 会话改为 ≥30 天并滑动续期。
- **`/api/sandbox/cases`（§12.2）**：只是可复用沙箱测试用例的 CRUD（json_store），本身不执行代码；hosted 保留，执行仍走正常 Runtime（会被沙箱化）。
- **`ModelEntry.capabilities` 字段集（§11.5）**：当前只有 `images: bool`。平台能力表只用 `images`，不凭空增加 `tools` / `thinking`。
- **firing 状态枚举（§12.6）**：`queued / starting / running / succeeded / failed / cancelled / missed / skipped`，没有 `skipped_quota`；实现配额时视语义需要新增或复用 `skipped`。

## 任务 2.3 暂缓删除账号表

`AUTH_SCHEMA_SQL` 和 `owner_user_id` 补列先留在 `ensure_db()`。账号写入和归属写入要到 3.2 / 3.8 才迁走；现在删掉的话，新库无法满足现有 INSERT，测试过不了。默认 Project 根已经改用 `default_project_root()`。

## 基线修复（开工前）

- `test_migration_preserves_instances_and_does_not_repeat`：cd43c6b 把预置模板缩成 DeepSeek 后，`_migrate` 静默丢弃模板之外的旧供应商实例。已改为按模板 ∪ 旧 providerConfigs 迁移。
- `test_provided_catalog_hides_keys_and_requires_session`：开发 `.env` 配了 key2api 令牌时走 key2api 分支导致空目录。fixture 固定清空令牌。
