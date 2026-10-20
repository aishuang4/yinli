# Tasks

## 1. 数据合同与模块边界

- [x] 1.1 在独立的 Skill 领域模块定义来源根、候选、已解析项、不可变快照和错误类型，分开安装状态与 resolved/shadowed/ambiguous/invalid/missing；验证序列化测试覆盖稳定 ID、loadName、来源、hash、拒绝原因，不把全文带入目录响应。
- [x] 1.2 为现有 Skill REST/TypeScript 类型和 Job manifest 增加向后兼容的 loadName、稳定来源与诊断字段，明确旧 ID 输入的兼容归一；用模型测试验证旧 payload 能读、新错误能区分，Job 的 required 集合不会扩大。
- [x] 1.3 建立 catalog、policy、reader、migration 的公开入口及旧 registry 兼容门面边界；验证依赖检查不新增 scheduler/subagents 对具体路由的反向依赖，不把新逻辑堆入 HTTP handler。

## 2. 文件根、统一解析与稳定身份

- [x] 2.1 将默认搜索根收敛为 `<tenant>/skills`，不扫描 Project、Workspace catalog 或 HOME 兼容 Skill 目录；用目录夹具验证 Project root、cwd 和 HOME 不会改变候选集合。
- [x] 2.2 将扫描统一为有界递归与包边界规则，排除依赖目录、支持目录和符号链接环；测试嵌套分类包可发现、包内 references/scripts 的 SKILL.md 不新增 Skill、重复 realpath 不重复，超限有诊断。
- [x] 2.3 实现唯一的 UTF-8/frontmatter 解析与原始字节 SHA-256，统一 BOM、缺省 name/description、非法 YAML/编码和文件不可读的结果；测试应用 catalog 与交给模型的元数据一致，缺 name 不再由 SDK 二次跳过。
- [x] 2.4 实现来源/owner/rootKey/相对路径生成稳定 ID，保留系统 ID并检测冲突；验证改正文仅改 hash、同正文不同脚本包保留不同 ID、绝对路径别名不改变同一包身份、目录改名无证据时旧引用失效。

## 3. 来源优先级与加载引用

- [x] 3.1 实现统一别名候选表，包含名称、目录名、相对路径、稳定 ID；测试统一根内同名包产生歧义，交换扫描和路径排序不改变结果。
- [x] 3.2 生成精确来源限定 loadName，处理 shadowedBy、候选消歧和保留系统名称；测试同层同名包可分别精确读取，系统保留项不能被冒充，SDK 接收的可执行名称无重复。
- [x] 3.3 固定“先冲突解析、后启停/授权过滤”的行为并保留精确引用入口；验证停用或未授权的精确引用明确失败，不按同名改指另一包。

## 4. 文件策略与旧数据迁移

- [x] 4.1 实现 Workspace `skills-policy.json` 的 schema、来源默认值、启停/共享/安装状态和有限别名映射；测试孤立策略不能制造包、Project 配置隔离、编辑正文不重置开关、损坏配置明确失败而非全启用。
- [x] 4.2 实现线程及跨进程文件锁、版本冲突处理与原子策略保存；用并发保存及故障注入测试验证启停/共享不互相覆盖、半写文件不会被读取成默认配置。
- [x] 4.3 实现只读迁移预检、备份和来源定位报告，读取旧 skills/JSON/overrides 及 profile/Job 位置证据；测试已有文件优先、只读外部包不修改、无 owner/路径/正文的记录保留失败诊断。
- [x] 4.4 实现旧包/配置分阶段幂等迁移，缺文件但有合法历史受管正文时仅在迁移阶段落包；测试中断重跑不复制、不覆盖用户文件、不提前提交完成 marker。
- [x] 4.5 建立按 owner 与路径证明的 legacy ID 映射供 Agent、Project binding 和 Job 引用归一；测试同名多候选/相同旧 hash 多来源不猜测，冻结 hash 与历史 Trace/SkillRun 原记录不被修改。
- [x] 4.6 切断新加载模式对旧 `_skills` 登记表、SQLite skills 行和旧 overrides 的实时读取/双写；用依赖注入将旧读源设为抛错，验证磁盘包仍可发现并加载、丢失文件明确失败，保留旧数据用于备份与回滚。

## 5. 公共 reader、AgentScope 与缓存

- [x] 5.1 实现接受已解析目标的统一正文 reader，核对来源、权限、文件版本并返回正文/hash/支持文件清单；验证文件缺失无 DB/内存正文回退、support files 不整包注入、包在选择后修改返回 stale/version 错误。
- [x] 5.2 实现应用侧 AgentScope Skill loader/viewer 适配，直接消费统一元数据和唯一 loadName，替换裸路径二次解析；用真实 Toolkit/Skill 工具测试验证索引列出的每个名称可读、同名不再后者覆盖、读取正文来自选中包。
- [x] 5.3 接入 Docker 的宿主包与运行目录映射，以稳定 ID 避免相同目录名覆盖；验证模型目录和脚本相对路径指向正确容器包，现有非持久 sandbox 限制继续生效。
- [x] 5.4 实现按租户/Workspace/Project/配置/根成员/文件签名隔离的 snapshot cache 和现有刷新失效路径；测试跨进程编辑、增删、策略变化在刷新/新 Run 可见，运行中快照变化不静默换包。

## 6. 管理读取与必要写入口适配

- [x] 6.1 将 `/api/skills` 列表、详情、分类、enabled、导出统一接入 resolver；API 测试验证无 DB 行中央包可打开/导出、Project 上下文不产生第二套包、invalid/ambiguous 不伪装可执行。
- [x] 6.2 将 `catalog/index.py` 和 Agent 可选目录改用同一解析与策略结果；验证 INDEX、管理页、选择器对来源、启停和冲突展示一致，不能继续使用扫描默认 enabled 覆盖用户设置。
- [x] 6.3 适配现有导入、注册、自进化写入，先在 `<tenant>/skills` 原子发布文件、保存策略、再从 catalog 返回结果；验证失败不返回成功或暴露半包，导入后不经 DB 登记也可立即读取。
- [x] 6.4 适配启停/共享/安装/卸载/删除到文件策略与受管包操作，保留既有 API 语义与系统锁定；测试卸载包不进入可执行目录、删除会清理策略旧别名，且 Skill 资源操作不扫描或同步暂停 Job。
- [x] 6.5 将系统 seed 从查询/Chat 读路径移至启动/升级阶段，增加 seed 签名及修改保护；验证重复查询不写盘、用户可修改包不被 rmtree 覆盖、系统保留说明书按受控来源更新。
- [x] 6.6 更新前端 Skill 类型/hooks/详情/Composer 选项，补齐上下文和加载名、来源、冲突原因；通过 TypeScript build 与桌面操作验证统一目录包可读详情/导出，保留既有动作顺序和管理入口。

## 7. Chat、子 Runtime 与激活正文

- [x] 7.1 将 Chat/Sandbox 的 selector、共享合并和强制 Skill 解析统一委托 catalog，不再自行按 ID/name 建覆盖字典；集成测试验证同一引用的包与管理接口一致、强制上限和共享/非共享行为继续生效。
- [x] 7.2 改造 `project_resources.py` 的 Skill owner/binding 判断，移除“SQLite 无资源就放行”的兜底；测试统一目录 Skill 按现有 Project 白名单裁剪，不能借缺 DB 行绕过校验。
- [x] 7.3 将 Team 已有 Skill 入口与 Subagent 显式引用接入统一加载器，保持其授权集合独立；测试 Leader/Member/子任务读取同一指定包，但 Member、群聊和无工具 Agent 不自动获得共享全集。
- [x] 7.4 在修改 Skill 生命周期 payload 之前更新 `docs/chat-sse-event-contract.md`，再让 loader 结果携带真实 ID/loadName/hash 贯穿使用记录、展示和 Trace；测试两个同名包不会“实际读 B、界面记 A”，不新增顶层 SSE 事件。
- [x] 7.5 将已激活 Skill 存储与恢复改为稳定 ID/source/hash，兼容持久化旧 name 记录时只做唯一映射；测试同名不混淆、压缩/实例重建/重启后恢复相同有效版本，无法映射时不猜测。
- [x] 7.6 在下一轮模型调用前校验旧激活记录，明确处理停用/解绑/缺失和普通 Chat 新版本；测试旧可控 Skill 块失效但用户原文与历史审计不被删除，新旧正文不同时宣称有效，required Skill 不被普通淘汰逻辑丢弃。

## 8. Job 全链路接入

- [x] 8.1 将 scheduler Skill capability inventory、feasibility、scripts/Bash 需求检测统一读文件 catalog 并应用 Job Agent 资格；测试启用门禁时无 DB 行中央 Skill 不再漏进库存或被错判缺失，同时不扩大工具权限。
- [x] 8.2 将 create/confirm/resume 的显式引用、contract 合并和 manifest 冻结使用统一 resolver；测试名称/精确引用一致，显式包缺失/歧义/不可读时确认失败，不能 active 且 required manifest 为空。
- [x] 8.3 将桥接预检、scheduled Context 和恢复使用同一冻结 ID/source/hash 读取结果；测试失败发生在 Agent/模型启动前，新同名高层包不改指旧任务，版本漂移、解绑和缺失错误进入现有 failure policy/通知。
- [x] 8.4 完成旧 Job ID/manifest 兼容与定义文件 round-trip，保留旧冻结 hash；测试唯一路径映射成功、无法映射则暂停/待确认、重确认才更新 hash，不自动复制 Agent 全量 Skills。
- [x] 8.5 跑通真实文件包的 daily 09:00 案例：feasibility → confirm → manual firing → 首轮 `<activated-skill>` → 第二次 fresh → 编辑后失败 → 重新确认；以模型 spy/可控测试模型证明预加载正确、system prompt 无正文、失败未调用模型，并核对 output manifest 与通知。

## 9. 整体一致性与迁移验收

- [x] 9.1 建立参数化入口矩阵，使用真实临时文件和空/脏 SQLite 夹具覆盖无行有文件、有行无文件、统一根同名歧义、项目目录忽略、设置保留、跨进程变动、解析默认值；验证列表、详情、INDEX、Composer、viewer、Job 的身份/来源/hash 一致，不只 mock resolver。
- [x] 9.2 覆盖设计 A9–A10 与权限负例：同名恢复、停用撤销、冻结来源不替换、符号链接越界、hosted HOME/跨租户、坏配置；验证失败不扩大权限或重新注入失效指令。
- [x] 9.3 用完整历史租户快照演练迁移、失败重试与回滚，包含旧 body-only、空 body、失效路径、只读包、重复 external ID、旧 bindings 和 active Job；交付逐条映射报告并验证包/开关/绑定/历史证据保留。
- [x] 9.4 进行桌面 QA：项目放包后刷新、详情/导出、Composer 按需与强制选择、同名冲突提示、文件编辑后刷新、Job 确认与版本失败；记录可观察结果，不能把目录出现或任务排队当成实际加载成功。

## 10. 文档、回归与交付

- [x] 10.1 更新 `docs/file-catalog-v1.md`、`docs/architecture-overview.md`、`docs/scheduled-jobs-v2-agent-activation.md` 与项目 TODO，说明新来源层、配置、loadName、文件权威和迁移限制；搜索确认不存在“只有注册入库才能加载”或按内容 hash 认身份的现行说明。
- [x] 10.2 运行相关 catalog/skills/native runtime/forced skills/context/subagent/scheduler/tenant/import-boundary 回归及后端全量 `uv run pytest`；记录实际结果，区分新失败与已有基线，不用只有单层 manifest 测试代替完整入口链路。
- [x] 10.3 运行前端 `npm run build`、`npm run lint`、`git diff --check` 与 `openspec validate unify-file-based-skill-loading --type change --strict`；完成规格—实现—测试逐项核对，有未完成验收时保留未勾选任务并报告限制。

## 11. 单根收敛与回归修复

- [x] 11.1 将管理和 Runtime catalog 收敛到 `<tenant>/skills`，停止扫描 Project、Workspace 和 HOME Skill 目录；验证真实用户数据只返回中央目录中的包，同名项目副本不再进入 SDK。
- [x] 11.2 将注册、导入、自进化和迁移补包统一写入中央目录，并更新 `yinli-catalog` 与正式文档，防止 Agent 再生成项目 Skill。
- [x] 11.3 让 catalog 诊断留在快照而不投影为“可安装 Skill”，删除受管包时清理 override、legacy alias 和 ownership。
- [x] 11.4 移除 Skill 启停、卸载和删除对 Job catalog 的反向扫描；修复 `create_job -> list_jobs -> sync_job_definitions -> create_job` 递归，Job 继续在 confirm、resume 和 firing 前校验冻结依赖。
- [x] 11.5 移除列表中的兼容提示标签并约束元数据列宽，避免启停按钮覆盖标签。
