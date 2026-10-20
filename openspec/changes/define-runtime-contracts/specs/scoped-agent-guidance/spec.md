# Spec Delta

## Purpose

编码智能体的指引按目录就近放置：根文件只做导航，每个子系统目录自己说明职责、不变量和钉住它们的测试。这样智能体只加载当前工作目录的规则，文档结构由测试保证不会脱节。

## ADDED Requirements

### Requirement: Root AGENTS.md is a map, not a manual

仓库根 `AGENTS.md` MUST 不超过 400 词，只包含：一句话产品定位、目录地图（每个有 `AGENTS.md` 的目录一行）、常用命令、全局硬规则（不超过 5 条）、"进入子目录前先读该目录的 AGENTS.md"。子系统的不变量 MUST NOT 出现在根文件。根 `CLAUDE.md` MUST 只含一行 `@AGENTS.md`。

#### Scenario: Root file stays within budget

- **WHEN** 统计根 `AGENTS.md` 词数
- **THEN** 不超过 400，且每个子目录 `AGENTS.md` 都在目录地图中有一行链接

### Requirement: Each subsystem directory carries a scoped AGENTS.md

以下目录 MUST 各有一份 `AGENTS.md` 与一行 `CLAUDE.md`（`@AGENTS.md`）：`backend/`、`backend/src/server/auth/`、`backend/src/server/routes/chat/`、`backend/src/server/routes/chat/team_runtime/`、`backend/src/memory/`、`backend/src/scheduler/`、`backend/src/skills/`、`frontend/src/features/runtime-stream/`、`electron/`。每份 MUST 按固定五段组织：职责与边界、入口、不变量、禁止、改动政策；不变量段的每一条 MUST 附至少一个以 `backend/test/` 或 `frontend/src/` 开头、文件实际存在的测试路径；单文件 MUST 不超过 800 词。现有根文件中找不到对应测试的不变量 MUST 迁入 `docs/invariants-without-tests.md` 而不是丢弃。

#### Scenario: Every required directory is covered

- **WHEN** 运行 `backend/test/test_agents_md.py`
- **THEN** 对上述每个目录都找到 `AGENTS.md` 与内容为 `@AGENTS.md` 的 `CLAUDE.md`

#### Scenario: Dangling test anchor fails

- **GIVEN** `backend/src/memory/AGENTS.md` 中某条不变量引用了不存在的 `backend/test/test_memory_nope.py`
- **WHEN** 运行 `backend/test/test_agents_md.py`
- **THEN** 测试失败，输出该路径和所在文件行号

#### Scenario: Oversized guidance fails

- **GIVEN** 某份子目录 `AGENTS.md` 超过 800 词
- **WHEN** 运行 `backend/test/test_agents_md.py`
- **THEN** 测试失败并报告当前词数

#### Scenario: No invariant is silently dropped

- **WHEN** 对比拆分前根 `AGENTS.md` 的每条规则与拆分后所有子目录 `AGENTS.md` 加 `docs/invariants-without-tests.md`
- **THEN** 每条规则至少出现在一处（允许改写措辞，不允许删除）
