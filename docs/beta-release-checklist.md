# YINLI Beta 内测发布清单

状态：用于 `0.1.0-beta.*` 桌面内测包发布前验收。

## 发布前自动检查

在仓库根目录执行：

```bash
cd frontend
npm ci
npm run lint
npm run build
npm run electron:compile
npm run release:preflight
cd ../backend
uv run pytest
```

macOS 构建机执行：

```bash
cd frontend
npm run electron:build:mac
```

Windows 构建机执行：

```powershell
cd frontend
npm ci
npm run electron:build:win
npm run release:preflight
```

Windows 必须在 Windows 原生环境构建后端 `backend-server.exe`，不能把 macOS 的
`backend-server` 复制到 Windows 安装包。macOS 同理必须使用目标架构构建后端。

## macOS 验收

- [ ] `.dmg` 拖拽安装后首次启动不出现空白页。
- [ ] 启动页最终显示后端就绪，Chat 可以发送最小请求。
- [ ] 设置页可以保存和重新读取模型 Key。
- [ ] 应用退出后内置后端进程退出。
- [ ] 重新启动后历史会话和设置仍在 `~/\.agent-workstation`。
- [ ] Apple Silicon 包使用 `arm64` 后端；Intel 用户需要单独的 `x64` 构建或 universal 包。

## Windows 验收

- [ ] NSIS 安装包可以安装到默认目录和自定义目录。
- [ ] 首次启动可以找到 `backend-server.exe` 并在 39100-39299 端口段启动。
- [ ] Chat、设置、文件夹选择和系统通知可用。
- [ ] API Key 保存到当前 Windows 用户 DPAPI 文件，SQLite 不含明文。
- [ ] 正常退出后没有残留 `backend-server.exe`；异常退出后重新启动可清理旧实例。
- [ ] 卸载选择保留数据后，重新安装可以恢复会话和设置。
- [ ] 卸载选择删除数据后，只删除 `%USERPROFILE%\\.agent-workstation` 和历史兼容目录。
- [ ] Windows Defender/SmartScreen 提示已在内测说明中明确；正式发布前补代码签名。

## 内测包限制

- 当前 macOS/Windows 安装包未做代码签名和公证，首次启动可能需要用户手动允许。
- 当前构建机必须与目标平台和架构一致；不要在 macOS 上声称已验证 Windows 安装包。
- 内测说明必须包含版本号、平台架构、已知限制、日志目录和问题反馈步骤。
- 安装包不得包含 `.env`、数据库、API Key、`backend-process.json` 或开发日志。
