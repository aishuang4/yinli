# Chat 模型选择与 models.json 配置 V1

> 状态：已落地，是现行模型配置合同。Windows 真机安装验收仍待执行。2026-09-07。

## 1. 目标与已知问题

- Agent 负责提示词、工具、Skills、权限和执行边界，不再选择供应商、主模型或 fallback。
- Chat 输入框底部右侧、发送按钮左侧放置模型选择器，位置参考用户截图。沿用现有 Agent/Team 控件，不引入额外 mode。
- 所有模型连接配置集中到用户数据目录的 `models.json`；设置页面负责编辑该文件。
- 一次选择必须同时确定供应商实例、adapter、Base URL、Key、模型和 fallback，不能仅用模型名称查找供应商。

现有 `ChatPage.tsx` 会把 Agent 展示模型作为请求 `model` 发送；`runtime_builder.py` 优先采用请求模型；`model_factory.py` 又单独读取全局供应商。这会产生过期模型与当前供应商的混合配置。这个缺陷已从代码确认，但仅凭此路径还不能证明用户反馈的“OpenAI Key 存在时始终调用 OpenAI”的唯一根因；应以实际请求目的地址验证。

## 2. Chat 与 Agent 交互

- 选择器收起时显示模型名和下拉箭头；菜单按供应商分组，同名模型显示供应商名称以区分。
- 菜单支持搜索、当前选中标记和进入工作台「模型」页。未配置 Key、停用或缺少地址的条目不可选。
- 新会话使用文件的 `defaultModelId`；既有会话记住自己的模型配置 ID。切换 Agent 不改变所选模型。
- 用户在 Chat 切换模型只影响本会话后续 Run，不更改其他会话或全局默认。
- 运行中或等待工具审批时锁定本轮模型选择；本轮结束后可修改下一轮模型。
- Agent 编辑、卡片、生成向导、导入导出移除主模型和降级链字段。ReAct 循环上限仍属 Agent；温度、topP、输出上限移至模型配置。
- Chat 标题、消息记录和 Trace 显示实际解析的模型信息，不能再使用 Agent 的旧字段。
- 图片能力使用模型配置的 capabilities 校验，不再写死某个模型名称。

## 3. 配置文件

路径为 `USER_DATA_ROOT / models.json`，由现有 runtime_paths 解析；源码开发模式为 `backend/models.json`；安装版 macOS 为 `~/Library/Application Support/agent-workstation-desktop/models.json`，Windows 为 `%APPDATA%\\agent-workstation-desktop\\models.json`，以 Electron 传入的数据目录为准。文件不放安装资源目录、仓库或 app.asar 中，升级不覆盖。

示例只含占位 Key：

```json
{
  "schemaVersion": 1,
  "revision": 1,
  "defaultModelId": "deepseek-flash",
  "providers": [
    {
      "id": "deepseek-official",
      "name": "DeepSeek",
      "adapter": "deepseek",
      "baseUrl": "https://api.deepseek.com",
      "apiKey": "<本地填写>",
      "enabled": true
    }
  ],
  "models": [
    {
      "id": "deepseek-flash",
      "providerId": "deepseek-official",
      "model": "deepseek-v4-flash",
      "label": "DeepSeek V4 Flash",
      "enabled": true,
      "fallbackModelIds": [],
      "contextSize": 0,
      "parameters": {"temperature": 0.7, "topP": 0.8, "maxTokens": 4096},
      "capabilities": {"images": false}
    }
  ]
}
```

- 供应商 ID 是连接实例身份；允许多个 OpenAI 兼容网关使用相同 adapter，分别持有地址和 Key。
- `contextSize` 是模型上下文窗口（tokens），决定 AgentScope 何时压缩上下文；`0` 或缺省表示按 adapter 默认（deepseek / openai / moonshot 128k、dashscope 131072、anthropic 200k、gemini 1M、ollama 32k）。降级链按链上最小窗口计算压缩阈值。
- 模型配置 ID 是引用身份，与供应商真实模型名分开；不同供应商可有同名模型。
- fallback 引用模型配置 ID，每个 fallback 使用自己的供应商、Key 和地址；拒绝缺失引用、循环及重复。
- 按用户要求 Key 也落在该文件中，因此是明文配置。前端读取接口仅返回已配置标记，不返回 Key；日志、Trace、导出 Agent、模型列表均不携带 Key。
- macOS 文件权限设为仅当前用户可读写；Windows 使用当前用户受限 ACL。模型配置文件排除在普通诊断包、Git 和安装包之外。
- 不再把 Keychain/DPAPI、环境变量或 SQLite 设置当作运行时补充来源，防止清除 Key 后隐式回退。

## 4. 读取、保存与运行绑定

- 模型管理读取、保存和每次新 Run 解析均读取文件，不依赖启动时常驻的模型配置副本。
- 文件校验统一放到配置服务：版本、唯一 ID、引用、参数范围、adapter 与地址要求一次校验。
- 后端保存使用锁、预期 revision/内容摘要校验、同目录临时文件写入、flush/fsync、原子替换。外部编辑与页面旧表单冲突时拒绝覆盖，提示重新读取；Windows 文件占用错误明确返回失败。
- 空文件、格式错误、缺失 Key、模型被删时明确报错，不能静默改用另一个供应商。首次安装可以初始化模板；已经完成迁移后文件丢失不能自动复活旧配置。
- Chat 请求只传 `modelConfigId`，后端解析为不可变的运行配置，包含完整连接和模型链。Run 创建成功前完成解析，模型工厂显式接收它，不再自行读取全局 provider。
- “每次读取文件”以新 Run 为边界：同一 Run 的主模型、重试、fallback、工具恢复、Leader/Member 和记忆后处理使用同一份配置。文件修改影响下一轮，当前 Run 不在中途切换。
- 运行缓存校验配置内容指纹，包含供应商身份、adapter、地址、模型、参数和凭据变化；凭据不写入日志或持久化缓存键。不能只用模型名命中旧 Agent。
- 持久运行记录只存不含密钥的模型身份和配置摘要。重启后如原配置已删除或发生不兼容变化，明确要求重新运行，不声称已恢复原连接。

## 5. 各执行入口

| 入口 | 模型来源 |
| --- | --- |
| Chat 单 Agent / Plan Mode | 本会话选择，进入 Run 后固定 |
| Chat Team | Leader 和临时 Member 继承本轮完整模型配置 |
| Attached Subagent | 默认继承父 Run；已有显式模型定义转换为明确模型配置 ID，歧义时报错 |
| Sandbox | 提供相同模型选择器，不从 Agent 获取模型 |
| 定时任务 | Job 保存模型配置 ID；未指定时在每次触发时读取全局默认；不能暗中继承最后一次 Chat 选择 |
| 群聊工作室 | 创建时记录明确模型配置 ID，Host/Worker 在各轮使用一致配置；不依赖全局可变 provider |
| readiness / intent router | 继承当前 Run 的完整模型配置 |
| 记忆与 Skill 后处理 | 继承触发它的 Run 配置，避免后台任务被全局切换影响 |
| Agent 生成向导及其他系统服务 | 调用开始时读取全局默认，生成的 Agent 不再保存模型绑定 |

## 6. 迁移与未提交补丁处理

- 有效 `models.json` 已存在时始终优先，禁止再次覆盖迁移。
- 首次迁移读取 SQLite 供应商设置和安全凭据库，将现有各供应商的默认、自定义和 fallback 模型转换为明确配置 ID，保留真实模型名大小写和自定义 Base URL。
- 安全凭据不可读时保留旧凭据，不自动删除；新文件标记该供应商需要重新配置，其余供应商可以完成迁移。
- 新文件原子写入并重新读取验证后，才记录迁移完成。旧存储仅保留迁移回退用途，运行时停止读取，清除 Key 后也不得重新导入旧 Key。
- 历史 Agent 模型字段不再参与运行；原数据暂留以避免破坏旧导出，不批量覆盖用户提示词、权限或工具配置。
- 历史会话缺少模型选择时使用文件默认配置；存在无法确定供应商的旧模型名称时，不做按名称猜测。
- 当前未提交的 `useSystemDefault`、同步默认 Agent 模型和请求字段补丁在实施本方案时整合移除，避免保留第二套模型优先级。无关既有改动保留。
- 不自动启动旧安装包回退运行。若需回退应用版本，需同时明确恢复哪套配置，不能让两个版本交替覆盖模型设置。

## 7. 验收

1. 用两个本地模拟供应商，同时配置两个非真实测试 Key，反复切换 Chat 模型，断言实际请求目标 URL、鉴权头、模型名全部匹配所选条目，不需清除任何 Key。
2. 两个供应商使用相同模型名时仍正确隔离；主模型失败后 fallback 使用被引用模型自己的连接配置。
3. Chat、Plan、Team、Sandbox、Subagent、定时任务和群聊入口按上述规则选模型；已有运行、审批恢复和后台后处理不因全局切换混用连接。
4. 外部修改文件后下一轮生效；坏 JSON、文件缺失、并发保存、文件占用、模型删除和 Key 清除均有明确结果，无隐式切换。
5. 默认模型选择、会话选择和配置迁移在重启后保持正确；设置读取与 Trace 不泄露 Key。
6. 浏览器验证输入框底部选择器、菜单展开、供应商分组、切换 Agent 保持模型、刷新恢复和禁用状态；核对长模型名不挤占发送按钮。
7. 后端全量测试、前端 lint/build；macOS 打包安装验证，Windows CI 验证文件路径、原子写入和 ACL。未执行 Windows 实机安装时必须注明，不能承诺无 Bug。

## 8. 本轮实施与验证记录

- 已移除 Agent 主模型、fallback 和采样参数入口；Chat / Sandbox 共用模型选择器。会话保存模型配置 ID，新建会话回到文件默认值。
- `models.json` 已成为在线唯一模型配置源，设置页支持供应商与模型增删、Key 修改与清除、默认模型、参数和降级链。旧 SQLite / 安全凭据仅参与一次性迁移。
- 双本地 HTTP 供应商测试确认：两个 Key 同时存在、同名模型交替选择时，实际 URL、鉴权头和模型正确；跨供应商 fallback 使用独立连接。
- 已覆盖配置快照、异步继承、审批恢复、缓存换 Key、迁移、外部修改、坏文件、并发保存、替换失败和 POSIX 权限。后端全量 `690 passed, 2 skipped`；最后 Windows ACL 调整后模型专项 `14 passed, 1 skipped`。
- 前端 `npm run build`、`npm run lint`、`npm run electron:compile` 通过；新增配置模块和测试 Ruff 检查通过，`git diff --check` 通过。
- 浏览器已验证 Chat 发起模拟模型请求、会话选择持久化与刷新恢复、模型管理新增与保存、保留已有 Key、新建会话默认值、切换 Agent 保持模型、Agent 编辑入口移除；1492px 与 1024px 宽度下长名称不会遮挡发送按钮。
- macOS arm64 PyInstaller 后端构建与启动通过；打包产物 `/api/health` 和模型目录接口均返回 200，缺少 Key 时返回 422 与明确说明，目录响应无 `apiKey`。
- 已添加 `.github/workflows/model-config.yml` 的 macOS / Windows 矩阵，覆盖请求绑定、文件锁、迁移、ACL、前端与 Electron 编译。当前尚未推送或执行远程 CI，Windows 专项在本机跳过。
- 本轮使用隔离数据和模拟供应商，没有覆盖现有安装版用户数据，也未执行完整安装包安装、真实供应商矩阵及 Windows 实机验收；这些仍是发布前检查项。
