# Scheduled Skill 实施与验收记录

变更：`simplify-global-session-context`  
项目：`/Users/aishuang/agent-workstation`  
日期：2026-10-19  
状态：Implementation Paused，90/94；本轮完成 8.9–8.17，不归档。

本轮将 scheduled Job 的 Skill 加载模式从普通交互按需加载中隔离出来。代码已经写入 Mac 项目，新增专项测试通过，但总体验收仍有环境限制、静态检查既有问题和需要真实数据/交互环境的未完成项目。

## 实施范围

- **模式隔离**：`interactive_on_demand` 与 `scheduled_preloaded` 进入运行时规格和 Agent 缓存键；工厂对旧缓存的模式不一致作防御性分流。
- **Toolkit**：scheduled Toolkit 不暴露内置 `Skill`/`SkillViewer`，保留 Skill 包挂载；原生工具可用性检查及直接执行入口均拒绝 viewer 调用。
- **目录与工具契约**：交互 catalog 提供稳定 ID、精确加载名、显示名称、描述和目录。`ScheduleJob.skill_ids` 接受稳定 ID，唯一名称兼容解析，拒绝歧义、未知引用和路径。
- **冻结快照**：模型工具 schema 不再暴露 `required_skills`。创建/更新时的客户端 manifest 不具权威性，不能覆盖 ConfirmJob 生成的冻结记录；旧客户端原样回传不触发重新确认。
- **确认响应**：ConfirmJob 回显实际冻结的 Skill ID、SHA-256、冻结时间、owner Session 和资源 grant 版本，并说明首轮模型调用前自动预加载。
- **首轮门禁**：preflight/hash → scheduled Toolkit 断言 → fresh Run snapshot/Context 检查 → forced Skill activation → ID/hash/正文完整性断言 → Job prompt → 首次模型调用；准备失败时不得调用模型。
- **审计与权限**：trace 和 firing 摘要记录注入顺序、预算、token 估算、恢复结果与被拒 viewer 调用，不重复保存正文；不扩大文件、工具、网络、MCP、审批或无人值守权限。

主要实现位于：

- `backend/src/agents/runtime_types.py`
- `backend/src/agents/skill_toolkit.py`（新增）
- `backend/src/agents/factory.py`
- `backend/src/runtime/agent_spec.py`
- `backend/src/runtime/scheduled_chat.py`
- `backend/src/skills/sdk.py`
- `backend/src/scheduler/{models,service,skill_manifest,firing}.py`
- `backend/src/catalog/sync_jobs.py`
- `backend/src/tools/scheduled_job_tools.py`
- `backend/src/server/routes/chat/{run_context,react_runtime,prompts,skill_context_middleware}.py`
- `backend/src/server/routes/chat/scheduled_skill_preload.py`（新增）
- `backend/src/server/routes/scheduled_jobs.py`

同时更新了 Job 能力合同文档、OpenSpec 任务状态，以及历史引用清单。历史引用清单仅登记三个新增兼容集成测试文件，没有放宽检查器规则。

## 规格—实现—测试映射

| 任务 | 实现要点 | 验证 |
|---|---|---|
| 8.9 | runtime mode、cache key、工厂缓存防御 | 同一 Agent 两种模式使用不同实例；两种工具 schema 不串用 |
| 8.10 | PreloadedSkillToolkit 关闭 viewer | scheduled 无 Skill/SkillViewer；普通 Chat 有 Skill；包仍可枚举 |
| 8.11 | scheduled 独立说明模板与恢复头 | 模板及真实首轮请求不含交互式加载指令；system prompt 无 Skill 正文 |
| 8.12 | catalog 稳定 ID、ScheduleJob ID 解析 | ID/唯一名称解析、歧义/未知/路径拒绝、catalog 字段检查 |
| 8.13 | server-owned manifest、ConfirmJob 回显 | 伪造 hash 不覆盖快照；旧回传保持 active；冻结 ID/hash/grant 回显 |
| 8.14 | 首轮预加载及 Context 完整性门禁 | activation/hash/body/duplicate/schema/history 六类失败不触达模型 |
| 8.15 | 工具层拒绝 viewer、沿用已有权限 | 模型实际发出 viewer 调用时拒绝；文件越界拒绝；工具/权限范围不扩大 |
| 8.16 | runtime audit、trace、firing manifest | 注入顺序、预算、完整性结果在 trace/firing 摘要中一致；压缩后受控恢复；无重复正文 |
| 8.17 | 固定 Skill 日报 Job 端到端路径 | ConfirmJob 后两次独立 firing 的真实模型首轮含同一冻结正文；普通 Chat 仍能按需使用 Skill；文件变更在模型调用前失败 |

新增测试：

- `backend/test/test_scheduled_preloaded_skill_runtime.py`
- `backend/test/test_scheduled_job_skill_contract.py`
- `backend/test/test_scheduled_preloaded_skill_e2e.py`

端到端测试使用真实 AgentScope、工厂、Toolkit、middleware 和 React runtime，只在模型网络调用边界使用 RecordingSkillModel。该测试不代表真实外部模型服务、真实脱敏数据迁移或交互式 UI QA 已完成。

## 验证结果

### 后端与 OpenSpec

- **新增专项测试**：19 passed。
- **最终定向回归**：37 passed，覆盖新增专项、global Session foundation、import boundaries。
- **后端全量**：1294 passed、2 skipped、4 failed。此结果来自最终全量运行，不将失败项计为通过。
- **严格规格校验**：`openspec validate simplify-global-session-context --type change --strict` 通过。
- **空白/冲突检查**：`git diff --check` 通过。

后端全量命令：

```sh
cd /Users/aishuang/agent-workstation/backend
.venv/bin/python -m pytest -q -p no:cacheprovider test
```

四项失败及实际限制：

| 测试 | 失败位置/限制 |
|---|---|
| `test_actual_http_switching_both_keys_and_identical_model_names` | 本地 HTTPServer 绑定 `127.0.0.1` 被执行环境拒绝，PermissionError |
| `test_actual_fallback_uses_own_provider_and_key` | 同类本地 HTTPServer socket bind 被拒绝 |
| `test_concurrent_process_save_only_one_revision_wins` | multiprocessing SemLock/进程锁被执行环境拒绝 |
| `test_local_bash_cancellation_kills_child_processes` | 子进程终止相关权限受限 |

以上是本次执行观察到的环境限制，不声称这些测试已经通过；需要在具有相应权限的终端复跑。

### 前端与 Electron

- **前端单元测试**：22 个文件、105 tests passed。默认 forks 模式因 `kill EPERM` 无法正常终止 worker，改用 `npm run test -- --pool=threads --maxWorkers=1` 后通过，未修改测试配置。
- **前端 lint**：0 errors、2 warnings，均为现有 `useGroupStudioRoom.ts` 的 React Hook 依赖警告。
- **前端 build**：通过，仍有大 chunk 提示。
- **Electron compile**：通过，包含 macOS 原生预览模块编译。
- **桌面/hosted 交互 QA**：未完成，不能以这些编译或单元测试结果代替。

### Python 静态检查

设备未安装 Ruff/Mypy，本轮没有向用户 Python 环境安装工具。将实际受影响源码的快照交给隔离 sandbox 中的 Ruff 0.17.1、Mypy 2.4.0 检查，并与本轮修改前的快照使用相同参数对照。

- **新增五个源文件/测试文件的 Ruff**：通过。
- **仓库 src 范围及新增测试的 Ruff**：999 条诊断；修改前同配置基线为 999 条；按文件/规则/诊断消息对照未发现新增诊断签名。整体命令仍非零，不能标为全仓 Ruff 通过。
- **18 个受影响源文件的 Mypy**：44 处错误、7 个文件；修改前对应 16 个源文件为 47 处错误、7 个文件。两项新增源文件在最终检查中无错误，整体命令仍非零。
- **Mypy 参数**：`--follow-imports=silent --ignore-missing-imports`。这不是严格模式的全仓类型检查，也不能声称全仓类型检查通过。

没有借机大范围修理仓库既有 lint/type 问题，也没有修改前端 Hook 逻辑。

## 未完成验收与安全门禁

- **12.6**：编译、单元测试和 lint 已执行；ego-browser 桌面/hosted 的 Session、目录授权、Chat、Job、群聊、删除保护、刷新恢复 QA 尚未完成，整项保持未勾选。
- **12.7**：未获取并确认真实脱敏历史数据集，未进行真实 dry-run、迁移、校验、cutover、rollback 演练；没有对用户历史库执行迁移。
- **12.8**：严格规格校验、diff 检查和本次 Skill 规格映射审查已完成；任务明确要求存在未验证迁移路径时保持未完成，因此仍未勾选，禁止旧表清理。
- **12.9**：专项测试及 import 检查通过，schema/prompt/ConfirmJob/firing audit 的语义已核对；全量仍有环境受限测试，Ruff/Mypy 整体非零，因此未标记专项总验收通过。

没有执行归档、旧表清理、历史数据 cutover 或破坏性迁移操作。

## 备份与证据位置

原有代码文件在写回前逐一校验 SHA-256；首次补丁的 18 个目标中，16 个原有代码文件均与快照一致，另两个为新增文件。原有代码备份位于：

```text
/Users/aishuang/agent-workstation/tmp/opsx-apply-backup-20261019-152335/
```

该目录的 `manifest.json` 记录首批补丁目标与原始哈希，另有文档和引用清单备份。任务文件的附加备份是在任务勾选更新后保存的，不是原始 81/94 状态的完整回滚快照。

本机执行证据保留在：

```text
/Users/aishuang/agent-workstation/tmp/opsx-apply/
  backend-final.log
  backend-final.exit
  final-targeted.log
  frontend-test-threads.log
  frontend-lint.log
  frontend-build.log
  electron-compile.log
```

建议后续先选择验收环境和范围：在有权限的终端复跑受限测试；提供桌面/hosted QA 环境与脱敏数据集；或单独批准仓库静态检查基线清理。全部验收通过前不使用 `openspec-archive-change` 归档。
