# Electron 桌面端打包规划

> 状态：内测打包能力已部分实现；正式打包、后端可执行文件、签名、公证和 Windows 真机验证仍待完成。

## 目标范围

- 支持 macOS：优先输出 `.dmg` 和 `.zip`。
- 支持 Windows：优先输出 `.exe` 安装包，后续可补免安装包。
- 前端、Electron 主进程、Python 后端、默认配置和静态资源可以随桌面端一起交付。
- 用户数据、SQLite 数据库、日志、Skills、MCP 配置等运行时数据必须写入用户数据目录，不写入应用安装目录。

## 分阶段策略

### 阶段一：内测打包

目标是快速验证 macOS/Windows 桌面端能跑通，不追求完全免环境。

- 用 `electron-builder` 打包 Electron 壳和前端 `dist`。
- 生产模式下 Electron 启动后端时，允许依赖本机已安装 Python/uv 或项目内 `.venv`。
- 明确提示内测机需要先完成后端依赖安装。
- 验证窗口启动、后端健康检查、Chat、Agent、Team、MCP、Skills、Settings、SQLite 持久化路径。

### 阶段二：正式打包

目标是面向普通用户，尽量做到安装后可直接运行。

- 将 Python 后端打成平台相关可执行文件。
- macOS 产物包含 `backend-server`，Windows 产物包含 `backend-server.exe`。
- Electron 主进程按平台选择后端可执行文件并启动。
- 后端数据目录迁移到 Electron `app.getPath('userData')` 下。为保持升级兼容，当前稳定目录名为：
  - macOS：`~/Library/Application Support/agent-workstation-desktop/`
  - Windows：`%APPDATA%/agent-workstation-desktop/`
- `.env` 仅作为首次初始化模板，真实 key 和模型配置继续走系统设置与 SQLite。

## 推荐技术路线

### Electron 打包

- 使用 `electron-builder`。
- 已在 `frontend/package.json` 增加：
  - `npm run electron:pack`：当前平台内测打包。
  - `npm run electron:build:mac`：生成 macOS `.dmg` / `.zip`。
  - `npm run electron:build:win`：生成 Windows `.exe` 安装包。
- 已新增 `frontend/electron-builder.yml` 配置。
- `extraResources` 包含：
  - `frontend/dist`
  - `frontend/electron-dist`
  - 后端可执行文件或后端源码/依赖
  - 默认配置模板

### 后端打包

优先评估 PyInstaller：

- 已新增入口脚本 `backend/src/server/desktop_entry.py`。
- 该入口负责读取桌面端传入的数据目录、端口、日志路径。
- 打包命令分别在 macOS 和 Windows 平台执行。
- 已新增 `scripts/build-backend-desktop.sh`，在目标平台执行后输出 `backend/dist/backend-server` 或 `backend/dist/backend-server.exe`。
- 如果 AgentScope 动态依赖导致 PyInstaller 不稳定，再评估 Nuitka。

### 主进程改造

`electron/main.ts` 需要补齐：

- 区分开发模式、内测打包模式、正式打包模式。
- 生产模式下解析 `process.resourcesPath`。
- 动态寻找后端启动入口：
  - 开发：跳过或连接本机 `localhost:8765`。
  - 内测：启动 Python/uv 后端。
  - 正式：启动平台后端可执行文件。
- 给后端传入：
  - `AGENT_WORKSTATION_DATA_DIR`
  - `AGENT_WORKSTATION_DESKTOP_MODE=1`
  - `AGENT_WORKSTATION_PORT`
- 后端退出时记录日志，并在异常退出时给前端可见错误。

## 平台注意事项

### macOS

- Apple Silicon 和 Intel 需要确认是否分别出包，或使用 universal 包。
- 正式发布需要代码签名和 notarization。
- 本地内测可以先关闭签名，使用未签名 `.dmg`。

### Windows

- 后端可执行文件路径和 shell 参数要避免 Unix 写法。
- 数据目录必须使用 `%APPDATA%` 或 Electron `userData`。
- 后续正式发布需要代码签名证书，否则会触发 SmartScreen 提醒。

## 验收清单

- [ ] macOS 能通过桌面应用启动完整工作台。
- [ ] Windows 能通过桌面应用启动完整工作台。
- [ ] 首次启动会创建用户数据目录。
- [ ] SQLite 数据库写入用户数据目录。
- [ ] 系统设置、Agent、Team、MCP、Skills、历史对话重启后不丢失。
- [ ] 后端异常时前端能显示清晰状态。
- [ ] 关闭桌面应用后后端子进程退出。
- [ ] 开发模式不受生产打包逻辑影响。
- [ ] 安装包不包含真实 `.env`、API key、用户数据库。
- [ ] 打包产物体积和启动时间在可接受范围内。

## 暂不处理

- 自动更新。
- 应用商店分发。
- 企业级静默安装。
- 多实例协同。
- 远程后端部署模式。
