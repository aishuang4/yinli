# Windows 用户数据卸载与设置入口 v1

> 状态：已实现，待 Windows 真机/虚拟机验收。本文只覆盖 Windows；macOS 本轮不增加卸载处理。

## 实施状态

已完成：

- Electron 主进程 `get-user-data-path` IPC；
- Preload 与前端 Electron 类型声明；
- 系统设置“用户数据”卡片、加载/失败/重试和打开目录交互；
- Windows NSIS 卸载确认、默认保留数据和固定目录清理；
- NSIS 脚本纳入版本控制并接入 `electron-builder.yml`；
- 前端 lint、production build、Electron compile 和后端全量测试。

待完成：

- Windows 安装包实际构建；
- Windows 安装、卸载、重装恢复和后端进程清理验收；
- Windows 权限不足/目录锁定时的残留数据提示验收。

## 1. 目标

为 Windows 安装版增加卸载时的用户数据处理选择，并在系统设置中显示实际用户数据目录，允许用户直接打开该目录。

## 2. 当前事实

- Windows 使用 Electron Builder NSIS 安装包。
- 安装版 Electron `userData` 当前固定为用户主目录下的 `.agent-workstation`。
- 后端数据、SQLite、日志和密钥都位于 Electron `userData` 或其子目录。
- Electron 已有受限的 `open-path` IPC，可打开用户主目录和 `userData` 下的已存在路径。
- macOS 使用 DMG/ZIP，本方案不为 macOS 增加卸载器或数据清理入口。

## 3. Windows 卸载行为

### 3.1 交互

NSIS 卸载开始时显示数据处理确认：

```text
是否删除引力 YINLI 的用户数据？

选择“是”将删除对话、Agent、Skill、MCP、Memory、日志和本地配置。
选择“否”只卸载应用，保留用户数据，重新安装后可以继续使用。
```

默认选择“否”，即保留用户数据。

### 3.2 执行顺序

1. NSIS 卸载器显示确认对话框。
2. 卸载应用文件，并由 Electron 应用自身负责正常退出时停止后端。
3. 若用户选择删除数据，删除当前稳定目录：
   - `%USERPROFILE%\\.agent-workstation`
4. 同时清理历史版本目录（若存在）：
   - `%APPDATA%\\agent-workstation-desktop`
5. 若目录删除失败，不阻断应用卸载；在卸载器日志或提示中说明仍有残留，避免误报为完全清理。

### 3.3 安全边界

- 仅 Windows NSIS 卸载脚本执行删除逻辑。
- 删除目标必须是固定目录，不接受用户输入路径，不使用宽泛的 `%USERPROFILE%` 或 `%APPDATA%` 递归删除。
- 默认保留数据，避免用户误删会话、记忆和模型配置。
- 卸载删除范围必须与设置页展示的“用户数据目录”一致，并在文案中明确包含 API Key 本地密钥。

## 4. 系统设置入口

### 4.1 展示

在“系统设置”右侧增加“用户数据”区域，仅桌面版显示：

- 标签：`用户数据目录`
- 内容：Electron `app.getPath('userData')` 的绝对路径
- 操作：`打开目录`

Web/Vite 浏览器模式不显示该区域，避免展示不存在的桌面数据目录。

### 4.2 IPC

新增最小 IPC：

- `get-user-data-path(): Promise<string>`：主进程返回 `app.getPath('userData')`
- 复用现有 `open-path` 打开目录

主进程仍执行路径白名单校验；设置页只允许打开主进程返回的路径，不自行拼接或执行系统命令。

### 4.3 打开失败

- 目录不存在时，主进程先创建目录再打开，保证首次启动可用。
- `shell.openPath` 返回错误时，设置页显示明确错误提示。
- 非桌面版不提供按钮。

## 5. 文件与实现边界

- `frontend/electron-builder.yml`：引用 Windows 专用 NSIS 自定义卸载脚本。
- `frontend/build/installer.nsh`：实现卸载确认和固定目录删除；该目录当前被 `.gitignore` 忽略，需要调整忽略规则以纳入版本控制。
- `electron/main.ts`：增加 `get-user-data-path` IPC，并确保目录存在。
- `electron/preload.ts`、`frontend/src/types/electron.d.ts`：暴露并声明 IPC。
- `frontend/src/pages/SettingsPage.tsx`：增加用户数据区域和打开目录交互。
- 不修改后端 API、SQLite schema、记忆模型或 macOS 打包行为。

## 6. 验收标准

### Windows 安装/卸载

- [ ] 安装后“应用和功能”中存在卸载入口。
- [ ] 卸载时显示用户数据确认。
- [ ] 选择保留后，卸载并重新安装，历史对话、设置和 Memory 仍存在。
- [ ] 选择删除后，当前目录和历史兼容目录均被清理。
- [ ] 卸载期间没有残留 `backend-server.exe` 进程。
- [ ] 数据删除失败时不误报为完全清理。

### 设置页

- [ ] Windows 桌面版显示实际绝对路径。
- [ ] 点击“打开目录”进入具体用户数据目录。
- [ ] 首次启动目录不存在时可自动创建并打开。
- [ ] 打开失败时显示可理解的错误。
- [ ] Web 模式不显示桌面目录入口。

## 7. 暂不处理

- macOS 卸载器、Finder 删除拦截和数据清理提示。
- 自动更新。
- 用户自定义数据目录迁移。
- 云端备份和恢复。

## 8. 完整实现流程

实现按“数据路径事实源 → Electron IPC → 前端设置入口 → NSIS 卸载 → 跨平台构建验证”的顺序进行。

### 8.1 第一步：冻结用户数据路径事实

Electron 主进程是用户数据路径的唯一事实源，前端和 NSIS 不自行推导业务路径。

当前安装版路径规则：

- Electron `app.getPath('userData')`：稳定目录 `path.join(app.getPath('home'), '.agent-workstation')`
- 当前目录下包含 `data/`、`logs/`、`secrets/`、`backend-process.json` 等运行时内容。
- 历史目录 `path.join(app.getPath('appData'), 'agent-workstation-desktop')` 仅用于升级迁移和卸载清理，不在设置页作为当前路径展示。

实现前先确认 `electron/main.ts` 中的 `app.setPath('userData', ...)` 执行顺序不变：必须在任何依赖 `app.getPath('userData')` 的目录创建、后端启动和 IPC 注册逻辑之前完成。

### 8.2 第二步：Electron 主进程新增用户数据 IPC

修改 `electron/main.ts`：

1. 新增 `ensureUserDataDirectory()`，调用 `fs.mkdirSync(app.getPath('userData'), { recursive: true })`。
2. 新增 IPC `get-user-data-path`：
   - 只返回 `app.getPath('userData')` 的绝对路径；
   - 不返回 API Key、目录内容或文件列表；
   - 生产模式和开发模式都返回当前 Electron `userData`，但前端只在桌面壳中展示。
3. IPC 调用时先确保目录存在，再返回路径。
4. 复用现有 `open-path` IPC 打开目录，不新增第二套系统命令执行逻辑。
5. `open-path` 继续调用 `resolveSafeLocalPath()` 做白名单检查；允许根目录本身存在时直接打开。
6. `shell.openPath()` 失败时返回 `{ ok: false, error }`，主进程写入桌面诊断日志，但不暴露堆栈和敏感路径以外的信息。

建议的 IPC 数据契约：

```ts
getUserDataPath(): Promise<string>
openPath(targetPath: string): Promise<{ ok: boolean; error?: string }>
```

不新增后端 `/api/settings/user-data` 路由，因为该路径属于 Electron 运行环境，不属于 Python 业务配置，也不应由浏览器客户端决定。

### 8.3 第三步：Preload 和前端类型层

修改 `electron/preload.ts`：

- 在 `contextBridge.exposeInMainWorld('electronAPI', ...)` 中暴露 `getUserDataPath`；
- 只暴露函数，不暴露 `ipcRenderer`、`app`、`shell` 或 Node.js 对象；
- 保持 `contextIsolation: true` 和 `nodeIntegration: false`。

修改 `frontend/src/types/electron.d.ts`：

- 在 `ElectronAPI` 接口增加 `getUserDataPath(): Promise<string>`；
- 保持返回值为字符串，避免把 Electron 类型泄露到 React 组件。

修改 `frontend/electron-dist` 的生成流程：

- 不手工编辑编译产物；
- 通过 `npm run electron:compile` 从 `electron/preload.ts` 和 `electron/main.ts` 重新生成 `frontend/electron-dist`；
- 提交策略遵循仓库现有约定，确保打包使用的 dist 与源码一致。

### 8.4 第四步：系统设置前端数据流

修改 `frontend/src/pages/SettingsPage.tsx`：

1. 新增状态：
   - `userDataPath: string | null`
   - `userDataLoading: boolean`
   - `userDataError: string`
   - `openingUserData: boolean`
2. 新增 `isDesktopShell = Boolean(window.electronAPI)` 派生值。
3. 页面初始化时：
   - 非桌面模式不调用 IPC；
   - 桌面模式调用 `window.electronAPI.getUserDataPath()`；
   - 失败时保留设置页可用，只在用户数据卡片内显示错误。
4. 新增 `loadUserDataPath()`，只负责读取路径，不和模型设置的 `loadSettings()` 混在同一个错误状态中。
5. 新增 `handleOpenUserData()`：
   - 没有路径时直接返回；
   - 设置 `openingUserData=true`，防止重复点击；
   - 调用 `window.electronAPI.openPath(userDataPath)`；
   - 返回失败时调用现有 `notifyError`；
   - `finally` 恢复按钮状态。
6. 页面卸载时不需要清理 IPC 监听，因为该功能是单次 Promise 调用，不注册事件订阅。

### 8.5 第五步：系统设置 UI 细节

在 `SettingsPage` 右侧设置卡片区域增加 `UserDataSettingsCard`，建议独立文件：

`frontend/src/features/settings/UserDataSettingsCard.tsx`

组件职责：

- 只接收 `path`、`loading`、`opening`、`error`、`onOpen`；
- 不直接读取 `window.electronAPI`，避免组件和 Electron IPC 耦合；
- 使用现有 `Card`、`Button`、`FolderOpen` 图标和通知样式。

显示内容：

- 标题：`用户数据`
- 标签：`用户数据目录`
- 路径：显示完整绝对路径；长路径使用可复制文本或 `title` 提供完整值，不截断实际内容；
- 说明：`对话、配置、Memory、日志和本地密钥保存在这里。`
- 操作按钮：`打开目录`，使用文件夹图标；
- 加载状态：显示 Skeleton 或 `读取中...`；
- 错误状态：显示 `暂时无法读取用户数据目录` 和重试按钮；
- 非桌面模式：整个卡片不渲染，而不是渲染一个不可用按钮。

布局要求：

- 与现有 `AppearanceSettingsCard`、`ConversationContextSettingsCard` 使用相同卡片样式；
- 路径区域允许换行，不能撑破右侧栏；
- Windows 桌面宽度下按钮和路径不重叠；
- 使用 `aria-label="打开用户数据目录"`；
- 按钮点击期间显示 loading，避免重复打开多个资源管理器窗口。

### 8.6 第六步：Windows NSIS 卸载脚本

新增并纳入版本控制：

`frontend/build/installer.nsh`

由于 `frontend/build/` 当前被 `.gitignore` 忽略，需要修改 `.gitignore`：

- 继续忽略生成的图标、DMG 背景等文件；
- 对 `frontend/build/installer.nsh` 增加例外规则；
- 确认 NSIS 脚本在全新 checkout 中存在，不能依赖本机历史生成文件。

在 `frontend/electron-builder.yml` 的 `nsis` 配置中增加 `include: ../build/installer.nsh`（路径以 electron-builder 的 `projectDir: desktop-package` 解析结果为准，必须在实际打包命令中确认）。

脚本实现建议：

1. 使用 electron-builder NSIS 的 `customUnInstall` 宏；
2. 弹出确认框，默认保留数据；
3. 用户选择“否”时跳过数据删除；
4. 用户选择“是”时只删除两个固定目录：
   - `$PROFILE\\.agent-workstation`
   - `$APPDATA\\agent-workstation-desktop`
5. 使用 `RMDir /r` 前检查目标路径非空且等于固定目录，禁止拼接用户输入；
6. 删除失败不回滚应用卸载，给出明确提示“应用已卸载，但部分用户数据未能删除”；
7. 不删除用户主目录、整个 `%APPDATA%` 或其他应用目录。

卸载提示文案必须说明删除内容包括本地 API Key；默认按钮选择保留，避免误操作。

### 8.7 第七步：后端边界

本需求不新增 Python/FastAPI 业务 API，不修改 SQLite schema，也不修改 Memory API。

后端只需要配合验证以下事实：

- `AGENT_WORKSTATION_DATA_DIR` 在桌面模式下确实等于 Electron `userData`；
- SQLite、Memory、日志和 secrets 都写入该目录或其子目录；
- 删除目录后重新启动时，数据库初始化可以重新创建默认 Workspace/Project；
- 保留目录后重新安装，历史数据可以继续读取。

若发现仍有运行时文件写入安装目录，必须在本需求实现前修正路径归属，否则卸载“删除用户数据”会留下不可见残留。

### 8.8 第八步：构建与产物

Windows 打包流程：

1. 使用 Node 20.19+ 或 Node 24；
2. 执行后端 PyInstaller Windows 构建，生成 `backend/dist/backend-server.exe`；
3. 执行前端 production build；
4. 执行 Electron compile，更新 main/preload dist；
5. 使用 `npm run electron:build:win` 生成 NSIS 安装包；
6. 检查安装包内包含 NSIS uninstaller；
7. 在干净 Windows 虚拟机或真机执行安装、运行、卸载测试。

macOS 构建流程保持不变，不引用 Windows NSIS 脚本，不增加卸载器。

### 8.9 第九步：测试矩阵

#### 前端静态与单元检查

- `npm run lint`
- `npm run build`
- Electron TypeScript compile
- 设置页在无 `window.electronAPI` 时不渲染用户数据卡片
- 设置页 IPC 失败时显示错误且模型设置仍可操作
- 打开按钮 loading 和失败通知行为

#### Electron 主进程检查

- `get-user-data-path` 返回绝对路径；
- 首次调用会创建目录；
- `open-path` 只允许 `userData`、home 和当前工作目录白名单；
- 越权路径返回失败；
- 路径不存在时不会调用 `shell.openPath`。

#### Windows 安装/卸载验收

- 安装后能启动前端、backend-server.exe 和 Chat；
- 设置页显示实际 Windows 路径并能打开资源管理器；
- 选择保留数据卸载，再安装后数据仍可读；
- 选择删除数据卸载，两个固定目录均清理；
- 应用运行中退出后再卸载，不能残留 backend-server.exe；
- 模拟目录锁定/权限不足时，卸载完成但提示部分数据残留；
- 卸载器不能删除用户主目录下其他文件。

#### 回归检查

- macOS 构建仍成功；
- macOS 不出现 Windows 卸载选项或 NSIS 文件；
- Web/Vite 模式设置页不请求 Electron IPC；
- `backend pytest` 全量通过；
- Electron 产物与源码 IPC 契约一致。

## 9. 实施顺序与提交拆分

建议拆成四个可独立审查的提交：

1. `docs`: 本方案和路径事实确认；
2. `electron`: 主进程 IPC、Preload、类型声明和安全校验；
3. `frontend`: 系统设置用户数据卡片和交互；
4. `packaging`: NSIS 卸载脚本、builder 配置、忽略规则和 Windows 验收记录。

每个提交完成后先运行对应的最小检查，最终再运行完整构建和 Windows 真机验收。

## 10. 已实现代码索引

- `electron/main.ts`：`userDataPath()` 和 `get-user-data-path` IPC；
- `electron/preload.ts`：安全暴露 `getUserDataPath()`；
- `frontend/src/types/electron.d.ts`：渲染进程类型声明；
- `frontend/src/pages/SettingsPage.tsx`：桌面模式加载用户数据路径并处理打开/错误状态；
- `frontend/src/features/settings/UserDataSettingsCard.tsx`：用户数据设置卡片；
- `frontend/build/installer.nsh`：Windows 卸载确认和固定目录删除；
- `frontend/electron-builder.yml`：NSIS `include` 配置。

## 11. 已完成验证

```text
backend pytest: 387 passed
frontend lint: passed
frontend production build: passed
Electron TypeScript compile: passed
git diff --check: passed
```

上述结果来自 macOS 开发环境；不能替代 Windows 安装包和卸载流程验收。
