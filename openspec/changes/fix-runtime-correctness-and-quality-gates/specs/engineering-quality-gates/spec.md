# Spec Delta

## Purpose

规定仓库在合入代码前必须通过的自动化质量门禁，包括后端静态检查、类型检查基线、全量测试、前端严格模式与零告警、运行时依赖边界和简体中文约定，使同类缺陷不会再无声进入主干。

## ADDED Requirements

### Requirement: One gate script defines the merge checks

仓库 MUST 提供唯一的质量门禁入口 `scripts/quality-gates.sh`，支持 `backend`、`frontend`、`all` 三个参数，任一检查失败时以非 0 状态退出。开发者本地、GitHub 工作流以及其他托管平台的流水线 MUST 调用同一个脚本，不得各自复制检查命令。代码合入主干前 MUST 通过 `scripts/quality-gates.sh all`。

#### Scenario: Local run matches CI

- **WHEN** 开发者在仓库根目录执行 `scripts/quality-gates.sh all`
- **THEN** 脚本执行的检查项与 CI 完全相同，失败时退出码非 0

### Requirement: Backend changes pass lint, type check and the full test suite in CI

门禁脚本的 `backend` 部分 MUST 依次执行：Ruff 静态检查、basedpyright 类型检查、类型基线不增长检查、后端全部测试。每个修改 `backend/**` 的拉取请求 MUST 在 CI 中通过这一部分，任一步失败 MUST 使检查失败。全量测试 MUST NOT 通过文件列表只跑子集；临时跳过的用例 MUST 以节点 ID 逐条列出，并注明负责修复的变更。

#### Scenario: Undefined name fails the build

- **WHEN** 拉取请求引入一个未定义名称的引用
- **THEN** Ruff 步骤失败，输出 `F821` 及文件行号

#### Scenario: A failing test anywhere fails the build

- **WHEN** 拉取请求使 `backend/test/` 下任意一个未被跳过的用例失败
- **THEN** 后端检查失败

### Requirement: Type errors may only decrease relative to the committed baseline

仓库 MUST 提交一份类型检查基线文件，记录引入门禁时已存在的错误。门禁 MUST 在出现基线以外的新类型错误时失败；当可以取到目标分支的基线文件时，当前基线条目数 MUST NOT 超过目标分支。开发者修掉基线内的错误后，基线 MUST 随之收缩；基线 MUST NOT 为了放行新错误而重新生成。

#### Scenario: New type error is rejected

- **WHEN** 拉取请求在一个没有基线条目的位置引入类型错误
- **THEN** basedpyright 步骤失败

#### Scenario: Regenerating the baseline to admit errors is rejected

- **WHEN** 拉取请求重新生成基线，使条目数比目标分支多
- **THEN** 基线不增长检查失败，输出两个条目数

#### Scenario: Existing errors do not block unrelated work

- **WHEN** 拉取请求只修改与基线错误无关的代码
- **THEN** basedpyright 步骤通过

### Requirement: Runtime dependencies exclude test tooling

后端 `[project].dependencies` MUST NOT 包含 pytest、Ruff、basedpyright、import-linter 等测试与检查工具；这些工具 MUST 放在 dev 依赖组。托管镜像和桌面后端打包 MUST 以不含 dev 组的方式安装依赖。

#### Scenario: Hosted image has no pytest

- **WHEN** 构建托管镜像后在容器内执行 `python -c "import pytest"`
- **THEN** 命令以 `ModuleNotFoundError` 失败

#### Scenario: Local development still has test tools

- **WHEN** 开发者在 `backend/` 执行 `uv sync` 后运行 `uv run pytest --version`
- **THEN** 命令成功输出版本号

### Requirement: Frontend changes pass strict type check, zero-warning lint and unit tests in CI

门禁脚本的 `frontend` 部分 MUST 执行，且每个修改 `frontend/**` 或 `electron/**` 的拉取请求 MUST 在 CI 中通过：开启 `strict` 的 TypeScript 检查、零告警的 ESLint、vitest 单元测试、生产构建与 Electron 编译。

#### Scenario: Lint warning fails the build

- **WHEN** 拉取请求引入一条 `react-hooks/exhaustive-deps` 告警
- **THEN** 前端检查失败

#### Scenario: Unit tests run in CI

- **WHEN** 拉取请求使任一 vitest 用例失败
- **THEN** 前端检查失败

### Requirement: Source text uses Simplified Chinese

后端源码、后端测试和前端源码中的注释、文档字符串和面向用户的文案 MUST 使用简体中文。仓库 MUST 有自动化测试，在上述文件中出现繁体字时失败；确需保留的繁体文本（例如测试繁简转换本身的用例数据）MUST 列入测试内的显式例外清单。

#### Scenario: Traditional characters are rejected

- **WHEN** 拉取请求在 `backend/src/` 下新增注释 `# 設定檔案`
- **THEN** 简体中文契约测试失败，并输出文件、行号和对应的简体写法
