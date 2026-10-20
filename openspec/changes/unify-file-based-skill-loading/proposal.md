# Proposal

## Why

当前 Skill 管理、Chat、Job 和 AgentScope 分别依赖数据库登记、项目目录、用户目录或包路径重新解析，已出现“存在且启用却无法加载”、同名重复和诊断记录冒充 Skill 的真实故障。需要采用文件目录发现与渐进加载，同时把应用 Skill 收敛到 `<USER_DATA_ROOT>/skills` 这一唯一根，消除数据库登记和项目包作为额外事实源。

## What Changes

- 将包的存在、名称、描述、正文和支持文件统一以磁盘 Skill 包为准；SQLite 不再决定是否可发现、可读取，数据库只保留运行记录及可重建投影。
- 统一以 `<USER_DATA_ROOT>/skills` 为唯一默认 Skill 根；注册、导入、自进化和迁移补包都写入该目录，Project、Workspace 和 HOME 兼容 Skill 目录不参与管理或运行时加载。
- **BREAKING**：外部包 ID 不再由 `SKILL.md` 正文 hash 决定，改为稳定的来源定位身份；旧 ID 通过有证据的迁移映射兼容，修改正文只改变版本。
- 引入明确的 `resolved` / `ambiguous` / `invalid` / `missing` 诊断；诊断保留在 catalog 快照中，不伪装成可安装 Skill。统一根内同名不同包不依赖扫描或路径排序猜测，模型使用无歧义的 `loadName`。
- Skill 管理列表、详情、导出、Agent 选择、Project INDEX、Chat、Composer 强制技能、Subagent、Team 已有 Skill 入口、Job 能力库存/确认/firing 复用同一 resolver 和 reader。
- 保留“目录先提供名称与描述，正文经 Skill 工具按需读取；Composer/Job 显式预加载”的交互，改为读取同一包；已读正文记录身份和版本，避免同名错误恢复。
- 将启停、共享及旧 ID 映射保存为独立文件配置；删除受管包时清理对应策略与旧别名。Skill 资源操作不反向扫描 Job，Job 在 confirm、resume 和 firing 前负责校验冻结依赖。
- 为历史数据库包及配置提供幂等、可中断恢复的迁移；不会用旧数据库正文覆盖已有包，不会凭同名更换 Job 冻结的包。

## Capabilities

### New Capabilities

- `skill-loading`：文件包发现、稳定身份、来源优先级、冲突解析、统一正文读取、文件变动感知和数据库去权威化迁移。

### Modified Capabilities

- `skills`：共享/启停配置与已解析文件包关联，所有管理和运行入口采用同一解析结果，保留不同 Runtime 的授权范围。
- `scheduled-job-skill-context`：Job 能力检查、冻结和 firing 共用文件加载器，冻结具体身份/来源/hash，迁移及同名覆盖不能静默切换包。

## Impact

- 后端：`src/catalog/skills.py`、`src/skills/registry.py`、Skill 管理路由、`project_resources.py`、AgentScope loader/Skill viewer 适配、Chat Skill 生命周期与恢复、Subagent、scheduler 能力库存和 manifest；新增职责独立的 catalog/policy/migration 模块。
- 写入口：现有 Skill 导入/注册/自进化、系统包 seed 和启停/共享保存须保证文件完成后刷新同一个 catalog；这是加载闭环所需兼容适配，不重新设计管理页面或任务系统。
- 前端/API：已有 Skill API 增加来源/冲突/加载名等字段并补齐 Project/Workspace 上下文；保留现有入口、动作顺序和字段兼容，不新增 Skill 工具并行协议，不改变 Chat SSE 架构。
- 数据：迁移现有包定位、启停/共享覆盖及绑定引用；保留历史 `skill_runs`、Job firing、审计与旧库备份，不在本变更中破坏性删除历史表。
- 边界：适配 YINLI 明确的 `Project.rootPath` 和租户隔离，不照搬 Hermes 的 Git 根探测、profile CLI、插件系统、独立项目信任交互或扫描服务；工具权限和 Job `fresh`/hash 校验继续生效。
