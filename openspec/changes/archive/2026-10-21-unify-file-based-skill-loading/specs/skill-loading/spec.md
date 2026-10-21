# Spec Delta

## Purpose

为 Skill 提供以文件包为唯一内容来源的统一加载能力，使管理目录、模型索引、按需读取、显式预加载及定时任务对同一技能的身份、来源、版本和拒绝原因保持一致，避免数据库登记与磁盘内容不同步造成不可读或错误加载。

## ADDED Requirements

### Requirement: Files determine Skill existence and content

系统 MUST 从当前允许的 Skill 文件根发现包，并以包内 `SKILL.md` 作为名称、描述和正文的内容事实源。有效且获准的包 MUST NOT 以存在 SQLite 登记行为加载前提；数据库登记、缓存或策略记录 MUST NOT 使缺失的包可加载。正常读取 MUST NOT 从旧数据库正文回退、补写包文件或触发系统包重新安装。

#### Scenario: Managed package without database registration

- **WHEN** 用户在 `<USER_DATA_ROOT>/skills` 放入有效且启用的 `daily-report/SKILL.md`，数据库不存在对应行
- **THEN** 管理目录、详情、允许使用它的 Runtime 和显式 Job 引用都能发现并读取同一个包，无需注册步骤

#### Scenario: Database row without package

- **WHEN** 历史数据库保留 Skill 信息，但其包文件已缺失
- **THEN** 系统返回缺失诊断，加载明确失败，不执行旧数据库或内存中缓存的正文

#### Scenario: Reading is read only

- **WHEN** 用户刷新目录或模型读取已有 Skill
- **THEN** 系统不删除、覆盖、重新 seed 或修改该包文件

### Requirement: Search roots are explicit and scoped

系统 MUST 只从当前 tenant 的 `<USER_DATA_ROOT>/skills` 发现 Skill。Project、Workspace catalog、进程 cwd、HOME 兼容目录及旧外部根配置 MUST NOT 产生第二套候选。扫描 MUST 有界并排除支持目录、依赖目录和符号链接环；hosted MUST NOT 扫描其他租户目录。

#### Scenario: Job work directory differs from Project root

- **WHEN** Job 的 firing cwd 位于项目中的输出目录
- **THEN** Skill 仍从租户统一应用根解析，不因 cwd 或 Project root 改变

#### Scenario: Project compatibility directories are ignored

- **WHEN** Project `.agents/skills` 或 HOME `.claude/skills` 包含 Skill
- **THEN** 它们不出现在管理目录或 Runtime catalog，不能覆盖统一应用根中的包

#### Scenario: Support files are not nested Skills

- **WHEN** 包的 `scripts/`、`assets/` 或 `references/` 中存在名为 `SKILL.md` 的支持文件
- **THEN** 它们不会被作为独立 Skill 暴露到目录

#### Scenario: Hosted source isolation

- **WHEN** hosted 请求或根配置试图引用宿主 HOME、别的 tenant 或未授权 Project 的包
- **THEN** 系统拒绝该来源，不泄露其目录和正文

### Requirement: Skill identity is independent of body version

系统 MUST 为每个来源明确的包提供与正文无关的稳定身份，正文版本 MUST 使用原始 `SKILL.md` 字节的 hash 单独表示。不同物理包 MUST NOT 仅因说明书内容相同而合并；用户启停、共享和授权引用 MUST 不因正文编辑而丢失或恢复默认。位置改变后的身份兼容 MUST 有明确的来源映射证据。

#### Scenario: Editing a disabled package

- **WHEN** 用户停用 Skill 并关闭共享，随后编辑同一目录下的 `SKILL.md`
- **THEN** Skill ID 和相关配置保持不变，正文版本改变，技能继续停用且不共享

#### Scenario: Equal manuals with different scripts

- **WHEN** 两个不同包的 `SKILL.md` 相同，但脚本或其他资源不同
- **THEN** 系统保留两个独立身份，不把其中一个包悄悄丢弃

#### Scenario: Rename without proven mapping

- **WHEN** 包目录改名且没有唯一可信的迁移映射
- **THEN** 原引用得到明确失效结果，不凭同名或相同正文自动改指新包

### Requirement: Skill aliases resolve deterministically

系统 MUST 对声明名称、目录名、相对包路径和兼容引用应用同一冲突规则。跨层裸名 MUST 选择最高来源层，低层记录 `shadowedBy`；同层不同包的相同别名 MUST 返回 `skill_ambiguous` 和可用精确加载引用。精确身份或来源限定引用 MUST 只选择指定包并继续执行授权检查。系统 MUST NOT 将候选顺序或底层 SDK 覆盖行为作为冲突规则。

#### Scenario: Project package cannot shadow managed package

- **WHEN** 当前 Project 与统一应用根存在不同的同名 `report`
- **THEN** 裸名 `report` 只解析到统一应用根中的包，项目副本不进入候选集合

#### Scenario: Same tier conflict

- **WHEN** 同一层存在两个不同包可响应裸名 `report`
- **THEN** 裸名加载失败并列出可区分的精确 `loadNames`，显式选择其中之一才能继续

#### Scenario: Reserved system identity

- **WHEN** 普通受管包试图占用系统保留的 Skill 身份或加载名称
- **THEN** 系统保留项仍指向系统来源，普通包返回保留名称冲突诊断

### Requirement: Every entry point reads one resolved Skill

管理列表、详情、分类、导出、选择器、项目索引、按需工具和显式预加载 MUST 使用同一文件解析规则。在相同 context 与引用下，允许加载的目标 MUST 具有相同身份、来源和版本；授权不同引起的拒绝 MUST 明确返回原因。模型索引给出的每个 `loadName` MUST 能通过同一 Skill 工具读取，工具不得再次按声明名称重新选包。

#### Scenario: Visible managed package can be opened

- **WHEN** 管理列表展示一个未登记数据库的有效受管 Skill
- **THEN** 详情和导出读取统一应用根中的同一个包，不返回仅因缺失注册行造成的 404

#### Scenario: Diagnostics are not installable Skills

- **WHEN** 扫描或迁移发现缺失旧引用、越界链接或其他目录诊断
- **THEN** 诊断保留在 catalog diagnostics，不计入 Skill 数量、不显示安装按钮，也不进入 Runtime

#### Scenario: Parser defaults match runtime

- **WHEN** 一个有效包没有显式 name，但目录名可用且具有有效描述
- **THEN** 管理和运行入口都采用目录名，不出现管理目录可见而实际模型 loader 跳过的差异

#### Scenario: Invalid manual is diagnosed

- **WHEN** 包编码、frontmatter 或可读性不满足加载要求
- **THEN** 管理接口提供 invalid 诊断，运行时拒绝加载，不把它显示为可直接执行的有效项

#### Scenario: Trace matches loaded package

- **WHEN** 用户通过来源限定引用加载同名候选中的一个
- **THEN** 展示与审计记录该包的实际身份、来源和 hash，不按声明名称记录另一候选

### Requirement: Ordinary Skill loading is progressive

普通 Runtime 的初始模型目录 MUST 只包含加载名、描述和必要来源路径，正文 MUST 在首次按需读取或显式预加载时进入会话。引用文件与脚本 MUST 保持按需访问；读取 Skill MUST NOT 扩大工具、文件、网络或审批权限。显式预加载与按需读取 MUST 使用同一个解析目标。

#### Scenario: Catalog does not preload every manual

- **WHEN** 单 Agent 目录包含多个可用 Skill，但用户没有强制选择它们
- **THEN** 初始 system prompt 不包含全部说明书正文，模型使用 Skill 工具读取所需包

#### Scenario: Composer pin matches manual read

- **WHEN** 用户在 Composer 显式选择某个可用 Skill
- **THEN** 预加载正文与通过其 loadName 按需读取的包一致，正文进入会话 Context 而非全局 system prompt

#### Scenario: Script does not grant tools

- **WHEN** 已加载 Skill 要求执行脚本，而当前 Runtime 不允许 Bash
- **THEN** 脚本执行仍被现有工具权限拒绝，不因 Skill 文件已加载而获得例外

### Requirement: Policy and legacy records cannot become another catalog

启停、共享、安装状态及兼容别名 MUST 使用独立的文件配置并引用稳定包身份；策略记录本身 MUST NOT 声明包存在。迁移完成后，旧数据库 Skill 行和 overrides MUST NOT 再参与实时加载决定。配置写入 MUST 避免部分成功和并发丢更新，配置损坏 MUST 明确失败而非恢复成默认全启用。

#### Scenario: Orphan policy entry

- **WHEN** 配置中仍保留已删除包的启停或共享条目
- **THEN** 该条目不会使技能重新出现在可加载目录

#### Scenario: Delete cleans policy without scanning Jobs

- **WHEN** 用户删除一个普通受管 Skill
- **THEN** 系统删除包并清理其 override 与旧 ID 别名，不扫描或同步暂停 Job；依赖 Job 在自身下次校验时失败关闭

#### Scenario: Policy file is corrupt

- **WHEN** 当前 Workspace 的 Skill 配置无法解析
- **THEN** 系统返回可操作错误，不默认打开受影响的动态技能

#### Scenario: Import success requires file success

- **WHEN** 用户导入或自进化注册 Skill，而包或策略保存失败
- **THEN** 操作不得返回成功安装，运行目录中不得出现可被误当成完整包的半成品

### Requirement: File changes invalidate catalogs consistently

系统 MUST 在新 Run 或显式刷新时感知来源成员、包文件及策略的变更，并按租户与运行归属隔离缓存。运行中的读取 MUST 校验快照对应版本，发生变化时明确报 stale 而非静默切换正文。只读刷新 MUST 不覆盖包或用户修改。

#### Scenario: Change made by another process

- **WHEN** 另一个进程新增、修改或删除包，随后用户刷新或开始新 Run
- **THEN** 所有读取入口使用反映磁盘变化的新目录，不要求重新登记数据库或重启服务

#### Scenario: File changes after catalog selection

- **WHEN** 模型已获得目录，但首次读取前该包内容发生变化
- **THEN** 该轮得到明确版本/目录过期结果，不能在无提示情况下加载另一版本

#### Scenario: Unchanged system package is read repeatedly

- **WHEN** 多次列目录或加载同一系统 Skill 且包未变化
- **THEN** 包文件不被重新复制或更新时间戳，用户修改也不被读操作覆盖

### Requirement: Activated Skill restoration respects identity and validity

已激活 Skill MUST 关联稳定身份、加载名、来源和正文版本，恢复时 MUST 复检当前授权与版本。停用、解绑或缺失的包 MUST 不再被自动作为有效指令补回；普通 Chat 的新版本更新 MUST 明确作废旧激活正文，不能因同名错误恢复其他包。历史消息和审计 MUST 不被伪造或删除。

#### Scenario: Disabled Skill after compression

- **WHEN** 用户停用已使用的 Skill，后续发生上下文压缩或实例重建
- **THEN** 旧正文不再被 middleware 自动补回并声称当前有效，拒绝原因可查询

#### Scenario: Same name different identities

- **WHEN** 一个会话先后明确使用两个不同来源的同名包
- **THEN** 恢复按各自身份和版本进行，不以相同声明名称覆盖或混淆记录

#### Scenario: Ordinary Chat sees an updated version

- **WHEN** 同一包内容修改后开始下一轮普通 Chat
- **THEN** 系统使用当前解析版本，旧激活指令明确失效，不同时把两个版本声明为当前有效

### Requirement: Legacy migration is recoverable and evidence based

迁移 MUST 保留现有包、用户配置、授权引用及历史运行证据；已有文件优先于数据库正文。旧 ID 兼容 MUST 以唯一来源/路径等证据为依据，不得按同名猜测。迁移 MUST 幂等且可在中断后继续，只有文件和配置验证成功才记录完成。缺失/歧义记录 MUST 保留诊断并阻止依赖任务误执行，不得触发自动扩大授权。

#### Scenario: File differs from legacy database body

- **WHEN** 现存包正文与旧数据库 content 不同
- **THEN** 迁移保留包文件，不能用数据库正文覆盖当前文件

#### Scenario: Legacy alias is ambiguous

- **WHEN** 一个旧 external ID 对应多个可能来源且无法唯一定位
- **THEN** 系统保留冲突报告，不把旧授权或 Job 引用自动绑定到任意候选

#### Scenario: Migration interrupted and retried

- **WHEN** 写包或写策略后进程中断，随后重新执行迁移
- **THEN** 系统继续未完成步骤，不复制出多份包、不丢配置、不提前标记完成
