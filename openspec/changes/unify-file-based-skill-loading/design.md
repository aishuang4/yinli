# Design

## Context

动机见 `proposal.md`。本设计采用 Hermes 的“文件根 → 候选目录 → 名称冲突解析 → 可用索引 → 按需/显式加载”结构，适配本项目已有的租户、Workspace、Project 和 Runtime 授权。

### 单根修订（2026-10-16）

本节覆盖下文仍提到的多来源层级、项目包优先级和 Workspace 写入位置：

- 应用只扫描 `<tenant>/skills`，Project `.agents/skills`、`.agent/skills`、`.claude/skills`、Workspace `.agents/skills` 和 HOME 兼容目录不参与管理或运行时加载。
- 注册、ZIP 导入、自进化注册、系统 seed 和历史正文迁移统一写入 `<tenant>/skills`。
- Workspace `skills-policy.json` 只保存稳定 ID 的启停、共享、安装状态和迁移别名，不声明第二个包根；Project 可用性继续由 binding 约束。
- 管理列表只投影真实包。扫描与迁移诊断保留在 `SkillCatalogSnapshot.diagnostics`，不得伪装成“可安装 Skill”。
- 删除普通受管包时清理 override、旧 ID 别名和 ownership。Skill 的删除、停用与卸载不扫描或同步暂停 Job；Job 在 confirm、resume 和 firing 前校验自己的冻结清单。
- Job 文件同步不得通过 `create_job -> list_jobs -> sync_job_definitions -> create_job` 重入；配额统计直接读取实例表。

已核对的实现事实：

| 位置 | 当前行为 | 本次必须解决的问题 |
| --- | --- | --- |
| `backend/src/catalog/skills.py` | 多目录扫描；外部 ID 取正文 hash；按 ID 去重 | 编辑正文改变身份；相同说明书、不同脚本的包被合并 |
| `backend/src/skills/registry.py` | SQLite 登记包与 external 扫描包分开；多个名称映射 | 文件存在不能保证所有入口可解析 |
| `backend/src/server/routes/skills.py` | 列表附加扫描包；详情/导出等仍以 registry 为主 | 列表、详情与执行不闭环 |
| `backend/src/scheduler/capability_catalog.py` | Skill 库存读取数据库行 | 项目包可能能执行却不进入可行性库存 |
| `backend/src/catalog/index.py` | INDEX 直接使用扫描默认启用值 | 与管理接口的启停覆盖不一致 |
| AgentScope 2.0.5 loader/toolkit | 再次解析 frontmatter；按 name 后者覆盖前者 | 应用层目录与模型目录不同；冲突受路径排序影响 |
| `skill_context_middleware.py` / `leader_skill_lifecycle.py` | 已读正文和展示记录主要按 name 匹配 | 同名包错误关联、旧正文错误恢复 |
| `catalog/skill_seed.py` | 每次 seed 删除再复制系统目录 | 读路径可能写盘，破坏文件缓存与用户修改 |

前一轮临时目录已复现：用户同名包覆盖项目包；停用外部包修改正文后恢复默认启用/共享；缺少 frontmatter name 的包出现在应用目录却被 SDK 跳过；说明书相同而脚本不同的包被扫描去重。上次 Job 补丁覆盖了项目包解析的局部缺口，没有统一以上入口。

参考材料为用户提供的 `hermes_skills_loading.md`，其中标注未核实的 Hermes 行为不作为已验证事实。本文中的优先级、身份和兼容策略是拟实施的 YINLI 决策。

## Goals / Non-Goals

**Goals:**

- 不经数据库注册，允许范围内的有效磁盘包即可被统一发现；数据库有记录但文件缺失时明确失败。
- 同一运行上下文、同一引用在管理接口、目录索引、实际 viewer、预加载和 Job 中得到相同身份、来源与版本。
- 文件编辑不重置配置；同名冲突、来源遮蔽、缺失和非法包可诊断。
- 保留 AgentScope 的 Skill 工具、工具权限边界与已有 Job 冻结/`fresh` Context 机制。
- 所有应用 Skill 统一落在受管根并提供稳定身份；历史引用有证据则兼容，没有证据则报错。

**Non-Goals:**

- 不复制 Hermes 的 profile/CLI、插件平台、Git 根查找、`.hermes` 专属目录或所有元数据扩展；不将本项目配置改成 `~/.hermes`。
- 不新增项目信任弹窗、内容安全扫描服务、Skill 市场或新的 CRUD 工具。Project 已有授权与读取范围继续作为边界。
- 不重做 Agent/Team/Memory/Job 产品，不让共享开关自动给 Job、Team Member 或群聊授权。
- 不把所有 Skill 正文预灌 system prompt，不在本次重做通用上下文压缩或引入新的 Context policy。
- 不以本次迁移为由删除历史运行表、重写历史 Trace，或把引用文件/脚本也宣称为已被 SKILL.md hash 冻结。

## Decisions

### 1. 文件包是唯一加载事实源，数据库退出登记门禁

新增统一文件 catalog 服务，依赖方向为 `routes/runtime/scheduler/subagents -> skills catalog/policy/reader -> filesystem/runtime_paths`。解析核心不得依赖 HTTP 路由；旧公开函数可以暂留兼容门面，但只能委托该服务，不能再次查询旧 registry 来判定存在。

SQLite 的 `skills`、旧 JSON namespace 和 `external_skill_overrides` 只在迁移阶段读取一次；切换完成后不参与实时发现、正文回退、默认启停或资源存在性判断。保留历史数据，允许可重建投影供查询使用，但投影缺失不能阻止磁盘包加载，投影也不能让缺失文件变得可加载。

**替代方案：**继续做 SQLite/文件双向同步，需要所有外部编辑都写回数据库，仍保留漏同步窗口；因此不采用。

### 2. 一个应用受管根，运行归属不依赖 cwd

搜索根由当前租户确定为 `<tenant>/skills`；不在模块 import 时绑定租户路径。Project、Workspace catalog、cwd、HOME 兼容目录和历史外部根配置均不生成候选。系统 bundled 包先安装到该目录，再由同一个 catalog 扫描；系统保留 ID/名称占用保留命名空间，不能由普通包冒充，`yinli-catalog` 的锁定共享契约不变。

Workspace 策略仍按稳定 ID 保存启停、共享、安装状态和旧 ID 映射，但策略不能创造包。Project 对统一包的可用性由 binding 与 Runtime 资格决定，不由包物理位置自动放行。

本次不递归搜索任意父目录或整个 HOME。扫描根的包发现统一为有界递归，遇到 `SKILL.md` 视为包边界，不再从该包的 `references/`、`templates/`、`assets/`、`scripts/` 发现嵌套 Skill；跳过 `.git`、`node_modules`、`.venv` 等支持目录并检测符号链接环。限定文件大小、扫描深度和候选数，超限报目录诊断，不假装完整扫描成功。

YINLI 不照搬 Hermes 的多根优先级和独立 trust/guard 扫描。统一根内的符号链接不得越过该根，hosted 不得引用其他租户。

### 3. 稳定身份、加载名、版本分离

定义职责单一的数据对象：

- `SkillSourceRoot`：来源层级、owner、逻辑 root key、实际路径、是否可管理。
- `SkillCandidate`：稳定 `id`、声明 `name`、包目录名、相对路径、来源、原始字节 hash、解析结果/诊断。
- `ResolvedSkill`：选中的 candidate、无歧义 `loadName`、可用状态、配置与授权结果。
- `SkillCatalogSnapshot`：来源集合、配置版本、文件签名、候选与别名解析表；一次 Run 使用不可变快照。

非系统 ID 采用 `skill-<sha256(ownerKey、rootKey、normalizedRelativePackagePath 的规范 JSON 数组) 的前 24 位>`，不能把正文 hash 混入；发现截短碰撞必须报错，不覆盖另一候选。标准根的 root key 不因该次实际绝对路径表示（如 macOS `/var` 与 `/private/var`）变化，路径大小写归一遵循实际文件系统，不能一律 lower。保留系统稳定 ID。目录改名/迁移属于位置变化，不承诺身份自动不变，只能通过显式迁移别名兼容。

`contentSha256` 为当次读取的原始 `SKILL.md` 字节完整 SHA-256；展示正文按统一解析结果得到，不把解析后去空格正文当版本依据。脚本和支持文件的变更参与资源缓存签名，但本次 Job 仍只承诺冻结 `SKILL.md`。

相同正文的两个不同包保留两个身份，不能据此合并脚本和权限。旧 ID 只是 `legacyAliases`，不能代替来源校验；映射必须租户/Workspace/Project 有界。

### 4. 统一别名冲突解析，禁止 SDK 再决定胜者

支持声明 name、包目录名、根内相对包路径、稳定 ID、迁移旧 ID 作为引用。精确 ID/来源限定 `loadName` 指定具体包，仍须通过启停与授权。

- 统一根内不同包同名：标 `ambiguous`，返回来源限定的可用 `loadNames`；不按文件名、扫描顺序或字典覆盖猜测。
- 即使两个包 `SKILL.md` 相同，只要 realpath 不同，也不证明是同一个包，不自动合并。
- 歧义允许通过精确 ID 或 `<rootKey>:<relativePackagePath>` 明确加载；旧 API ID 参数通过同一 resolver 解释，不能开放任意路径读取。
- 先解析别名，再做启停/授权过滤；返回具体拒绝原因，避免静默换包。

管理列表只展示真实文件包；扫描和迁移诊断保留在 catalog 快照或专用诊断面，不得包装成“可安装 Skill”。模型索引只展示可执行项及明确的限定加载名。`Skill` 工具的可接受名称集合必须与索引完全一致；给 AgentScope 的 `name` 使用 `loadName`，原声明 name 留作显示，不再交给其 `OrderedDict` 后者覆盖策略处理重复名。

**替代方案：**只按 name 建一个 dict 仍会丢来源；只把所有包改成强制命名空间会破坏现有裸名使用习惯。采用“裸名便捷解析 + 精确加载名消歧”。

### 5. 一个解析器和 reader，所有入口共用

拟提供公开领域入口：`build_skill_catalog(context)`、`resolve_skill(ref, catalog, access)`、`read_skill(resolved, expected_hash=None)`。包解析仅做一次：UTF-8（兼容去 BOM 解析）、safe YAML、name 缺省目录名、description 缺省正文首个非标题文本段；无可用描述、非法 YAML 或不可读包返回 `invalid`/可识别错误，不制造“可用”条目。

`read_skill` 返回已选包定位、原始 hash、统一正文及支持文件清单。正文只来自包文件，不能回退 `skills.content` 或旧进程缓存。普通按需读取发生在 Skill 工具调用时；磁盘预解析/缓存不等于将正文送入模型。支持文件只列举或按需通过现有 Read/Bash 等工具访问，不整包注入。

AgentScope 通过应用侧 `SkillLoaderBase`/viewer 适配接收已解析快照与唯一 loadName；不再用裸路径让 `LocalSkillLoader` 执行另一套元数据和名称选择规则。实际使用的包身份及 hash 同时进入 Skill 生命周期记录，不能按声明 name `next(...)` 猜一条展示记录。Docker 仍映射到 `/workspace/skills`，目录映射按稳定 ID 避免同目录名覆盖。

接入矩阵：

| 入口 | 所需一致行为 |
| --- | --- |
| 管理列表/详情/分类/导出/Agent 选择、Project INDEX | 相同 context 下能查到统一应用根中的同一个包和状态；无 DB 行仍可读详情和导出 |
| Chat/Sandbox/Team Leader | resolver 候选先过既有资源与共享规则；模型按需读正文 |
| Composer 强制 Skill | 精确引用当前选项；不依赖共享开关；仍遵守启停、Project、数量上限 |
| Team Member/Subagent | 相同 parser/reader；仅处理被显式授予的引用，不继承共享全集 |
| 群聊/无工具基础设施 Agent | 不因加载器统一增加任何 Skill |
| Job 能力库存/feasibility/confirm/resume/firing/恢复 | 同一文件目录和引用解析；不再分别查 SQLite 与项目包 |

`project_resources.py` 的 Skill 分支改用稳定 ID 与 binding 判断；不能继续用“SQLite 查不到就允许”或“位于项目目录就放行”的宽泛兜底。

### 6. 文件配置记录策略，缓存永远可重建

每个 Workspace 使用 `<tenant>/workspaces/<workspace_id>/.agents/skills-policy.json`，版本化记录以稳定 ID 为键的 enabled/availableToAllAgents/安装状态覆盖、受控旧 ID 别名和迁移诊断。此文件不复制包名、描述、正文或全部包列表，孤立策略项不代表 Skill 存在；系统共享锁定由保留规则执行。

沿用现有“卸载后包可保留且再次安装”的产品语义：策略可将磁盘包标为 unavailable，不因此删除它；没有该配置的有效项目/Workspace 包按来源默认可用。删除入口只删除受管包并清理策略，不允许通过项目只读包的详情接口删除任意路径。

采用线程锁与跨进程文件锁保护“读取版本—检查—临时文件—原子替换”全过程，文件锁按平台使用已有可复用设施或封装 `fcntl`/`msvcrt`，不把单独检查版本后 rename 当成跨进程事务。避免两个设置保存相互覆盖；配置 JSON 损坏时明确报 `skill_policy_invalid`，该 Workspace 的动态加载 fail closed，不能重置成默认全启用。

快照 cache key 至少含 tenant、Workspace、Project、规范根集合、配置版本、Runtime 资格和候选文件签名。根目录成员变化必须被发现，不能只统计已知文件。复用原始文件的 `mtime_ns/size/ctime_ns` 等签名；首次/显式刷新及 Job hash 校验读取真实字节，不把时间戳当版本证明。

每个新 Run 重建/校验签名；现有列表刷新立即失效并重扫。运行中使用快照，读正文时与快照版本不一致返回 `skill_catalog_stale`；下一轮或用户刷新重建，禁止同一轮悄悄换成新包。跨进程依靠磁盘签名与配置版本感知，不要求 filesystem watcher，不仅依靠当前进程 invalidate。

### 7. 普通 Context 与 Job 使用同一身份/版本，但保留不同策略

已激活记录由 name 键调整为稳定 ID，记录 loadName、source、hash、读取正文、激活时间和 required 标志；显示 name 不再承担身份。恢复前根据当前授权 catalog 校验：停用/解绑/缺失的不再自动补回旧正文；已存在的历史消息不删除或伪造，只禁止把其当作当前有效 Skill 重新激活。

普通 Chat 在下一轮采用当前版本：重建有效 catalog，作废旧激活记录；对旧 Skill tool result/HintBlock 的可控上下文块标记为旧版本或替换为明确历史存根，再通过同一个 reader 获取新版本。仅允许处理已标记的 Skill 块，不清洗用户原始消息或普通产物。更新失败返回明确错误，不同时宣称新旧两版均已激活。审计记录版本变化。

Job 在确认时将用户引用规范化为具体包稳定 ID、来源定位与原始 hash；空 required 集合仍合法。有显式 Skill 却不能定位/读取时必须确认失败，移除现有“无包路径仍 active 且 manifest 为空”的兼容绕行。

firing 对冻结 ID 读取原包，不能因裸名出现更高层版本或同名包替换而自动换目标；权限和启停始终复检。hash 改变返回 `skill_version_changed`；缺失 `skill_missing`；歧义 `skill_ambiguous`；身份无法兼容 `skill_metadata_missing`；来源不符 `skill_source_changed`。错误在模型调用前收口。重新确认前，展示当前可解析目标与差异；不得自动改写已确认的 hash。继续使用现有 fail-closed 预算、`fresh` session、通知和工具边界，不另造 Job Runtime。

### 8. 写入口只适配文件事实源，读取不得反向重建包

保持既有导入、注册、删除、启停与自进化 API。导入/注册/自进化先验证包并在 `<tenant>/skills` 原子落盘，保存独立策略后刷新 catalog；返回值取自统一 resolver，不再先写 SQLite 才能加载。部分写入失败不返回成功，不留下可被扫描成成功安装的半包。

系统 seed 不得由目录查询/Chat 每轮触发删除复制：移动到启动/升级准备阶段，记录上次 seed 内容签名；文件未变就不写。用户改过的可修改系统包不覆盖，升级候选另存并提示；系统保留 `yinli-catalog` 的权威说明书只在系统管理路径受控更新，不允许用户同名包覆盖。不能把同名路径无条件 `rmtree` 当成读逻辑的一部分。

### 9. API、展示和错误兼容

继续使用 `/api/skills`；详情、导出、分类、enabled 目录以及必要的操作请求统一携带/解析 Workspace、Project。旧调用缺省上下文时只读其默认 Workspace，不允许通过 ID 猜别的租户/Project。新增可选 `loadName`、来源、解析状态、shadowedBy、冲突候选、错误码与 catalog 版本；现有安装状态字段不重载为解析状态，避免前端枚举混用。

现有 `Skill` 工具名和输入 `skill` 字段保留，值接受索引列出的 loadName。详情/导出只读取统一应用根中的包。管理界面只展示真实包并保留现有动作顺序；诊断不伪装成可安装 Skill。若 Skill 生命周期审计或 Chat payload 发生语义变化，先更新 `docs/chat-sse-event-contract.md`，继续走已有稳定事件与 Block 投影，不新增顶层 SSE 类型。

## Risks / Trade-offs

| 风险或陷阱 | 处理与验收 |
| --- | --- |
| 编辑正文导致旧 external ID 无法对上 | 由来源/路径建立一次性别名；不能单靠同名或 hash 认领 |
| 相同旧 hash 对应多个包 | 迁移标记歧义、保留配置，不任意选择或扩大授权 |
| 两个目录同名但说明书相同、脚本不同 | 保留不同身份，同层报歧义，精确引用加载各自包 |
| 停用包被同名包替代 | 先解析身份后过滤，明确报 disabled，不按同名改指 |
| 页面读到旧库缓存、工具读到新文件 | 管理/加载共用快照；读文件时版本变化报 stale |
| 缺少 name 被 SDK 再次拒绝 | 唯一 parser 补 name，应用 loader 直接传解析结果 |
| 包含非法 YAML/编码、读权限异常 | 可观测 invalid，不伪造完整可用项；无数据库正文兜底 |
| 跨 Workspace 同名、hosted HOME 泄露 | 先按 owner 构建根再解析；缓存分租户；hosted 不扫描 HOME |
| 项目目录残留 Skill 包 | 不扫描、不修改，也不进入管理或 Runtime catalog |
| 配置损坏被当成空设置 | 明确失败，不默认重新打开全部技能 |
| Agent 缓存换实例但旧正文被 carry-over | 恢复后、模型调用前按 ID/hash/授权清理失效激活块 |
| Job 迁移后同名包被替换 | 保留原冻结定位与 hash，无证据则暂停/待确认，模型不运行 |
| seed 删除用户修改、导致每轮缓存失效 | 查询只读；升级与扫描分离，按 seed 签名保护修改 |
| 多进程导入/策略写入相互覆盖 | 原子包发布、配置版本冲突重试或明确 409 |
| 误把迁移完成当成测试完成 | 必须验证无 DB 行可用、有 DB 行无文件失败，以及实际 Skill viewer 的包选择 |

## Migration Plan

1. **备份与预检**：备份当前租户的 Skill 表/旧 overrides、受管包、profiles、Project bindings 与 Job 定义/manifest；生成逐条来源与路径映射报告，不修改外部只读包。
2. **先建立文件目录与新策略**：已有包不搬迁也不覆盖，以真实路径/owner 生成稳定身份；新的受管包落 Workspace 根。把启停、共享和安装状态映射到新策略文件。没有额外设置的新磁盘包无需登记即可可用。
3. **处理旧正文和丢失包**：仅对确实没有文件且仍有可导出正文的历史受管记录，执行一次显式迁移写包并验证。已有文件永远优先；正文空、位置不明、owner 不明或路径不允许的记录作为迁移失败保留，不在正常读取时补包。
4. **兼容旧引用**：按 DB resourcePath、profile/Job 来源定位和现存包证据生成有限别名映射；各绑定入口先规范化旧 ID 再校验 owner。历史 SkillRun/Trace 原 ID 保持可解释，不重写历史证据。无法唯一定位的配置列入报告，依赖的 Job 暂停/待确认，不用同名替换。
5. **分批切换但单一读源**：先切 catalog 与管理只读接口，再接 viewer/Chat/Composer/Subagent/Team，最后接 scheduler 全链路；部署开关以整个租户为单位切换，同一请求不得一部分读旧 registry、一部分读新 catalog。迁移 marker 只有写文件并 round-trip 校验完成后才提交。
6. **崩溃恢复与去旧权威**：记录分阶段 checkpoint，重跑按路径和配置版本幂等；失败不得删除旧库/包。新模式不再运行 `_load_user_skills`/`_save_user_skills` 的登记加载流程，旧表留作备份/历史，不能继续接受实时双写以免回到两个事实源。
7. **验收与回滚**：完整临时租户和桌面 UI 验收通过后切换。回滚恢复成套备份及旧部署；新模式运行后新增的文件/策略先导出并隔离保留，不直接启动旧代码覆盖它们，也不试图同时维护两套 live registry。

## Acceptance Examples

| 编号 | 操作 | 必须观察到的结果 |
| --- | --- | --- |
| A1 | 不写 DB，直接放入 `<tenant>/skills/daily-report/SKILL.md` | 列表/详情/Composer/viewer/Job 显式确认解析同一包 |
| A2 | 保留旧 DB 行，移除包文件 | viewer/Job 明确失败，无旧正文执行；管理列表不出现伪 Skill |
| A3 | 中央目录和项目目录各放不同版本同名 report | 只加载中央目录版本，项目副本不进入 catalog 或 SDK |
| A4 | 中央目录内两个不同包声明同名 | 裸名报 ambiguous，精确 loadName 可区分；不受排序影响 |
| A5 | 停用且关闭共享后编辑正文 | ID、停用和共享配置不变，hash 改变 |
| A6 | 新增/修改/删除文件后刷新，另一个进程读取 | 新快照可见变化，不能继续用仅进程缓存的旧 catalog |
| A7 | 有 description、无 name 的包 | 目录名成为统一有效 name，模型目录和工具读取一致 |
| A8 | Job 每日 09:00 冻结中央目录日报 Skill | 首轮预加载正确正文；第二次 fresh；修改说明书后模型调用前 version_changed |
| A9 | 重启/压缩恢复时停用 Skill 或切换版本 | 旧版不重新激活；当前版本/拒绝原因可审计 |
| A10 | hosted 租户 B 引用租户 A 或宿主 HOME 路径 | 被拒绝且无目录/正文泄露，不因 SQLite 无行而放行 |
