# Spec Delta

## Purpose

规定 Electron 桌面外壳在打开外链、主窗口导航、页面脚本来源和打开本地路径时的安全边界，防止聊天内容、模型输出或工具结果里的链接和路径被用来启动本机程序或把主窗口带离应用页面。

## ADDED Requirements

### Requirement: External links open only with allowed schemes

主窗口里的 `window.open`、`target="_blank"` 链接和 IPC `open-external` MUST 使用同一份白名单：协议为 `http:`、`https:`、`mailto:` 的地址，以及主机为 `open` 的 `obsidian:` 地址。命中白名单的地址 MUST 交给系统默认程序打开；其他任何协议（包括 `file:`、`smb:`、`javascript:`、`data:` 和第三方自定义协议）以及无法解析的地址 MUST 被拒绝，应用 MUST NOT 打开任何程序或新窗口。

#### Scenario: Web link opens in the system browser

- **WHEN** 用户点击 Markdown 回复里的 `https://example.com/page`
- **THEN** 系统浏览器打开该地址，应用内不创建新窗口

#### Scenario: File and custom scheme links are refused

- **WHEN** 页面调用 `window.open('file:///Applications/Calculator.app')`、`window.open('smb://host/share')` 或 `window.open('vscode://file/x')`
- **THEN** 应用不打开任何内容，并在主进程日志记录一条被拒绝的协议名（不记录完整地址）

#### Scenario: Obsidian deep link keeps working

- **WHEN** 页面通过 `open-external` 或 `window.open` 打开 `obsidian://open?vault=x`
- **THEN** 系统交给 Obsidian 打开

### Requirement: Main window stays on the application page

主窗口 MUST 拦截 `will-navigate` 和 `will-redirect`。目标地址不是应用自身入口（打包版为 `dist/index.html` 的 file 地址，开发版为 Vite 开发服务器 origin）时，MUST 阻止导航；若目标地址命中外链白名单，MUST 改为交给系统默认程序打开。目标地址去掉 query 和 hash 后等于应用入口的导航（例如刷新或 `?view=` 变化）MUST 放行；应用内通过 `history.pushState`/`replaceState` 切换 query 的状态变化 MUST 不受影响。

#### Scenario: In-page link cannot replace the app

- **WHEN** 一个没有 `target` 的 `<a href="https://attacker.example">` 触发主窗口导航
- **THEN** 主窗口仍停留在应用页面，系统浏览器打开该地址

#### Scenario: In-app query state changes still work

- **WHEN** 应用通过 `history.replaceState` 把地址从 `index.html?view=chat` 改为 `index.html?view=runs&runId=r1`，或用户刷新主窗口
- **THEN** 页面状态正常切换，刷新后仍停留在应用页面

### Requirement: Packaged renderer enforces a Content Security Policy

打包构建生成的 `dist/index.html` MUST 包含 `Content-Security-Policy` meta，至少限定：`script-src` 只允许应用自身文件且不含 `'unsafe-inline'` 和 `'unsafe-eval'`；`object-src 'none'`；`base-uri 'none'`；`connect-src` 允许内置后端。开发构建 MUST NOT 注入该 meta。

#### Scenario: Injected inline script does not run

- **GIVEN** 打包版应用
- **WHEN** 页面中被插入一段内联 `<script>`
- **THEN** 脚本不执行，控制台出现 CSP 违规提示

#### Scenario: Normal pages load without violations

- **GIVEN** 打包版应用
- **WHEN** 依次打开对话、群聊、运行追踪、文件预览（Markdown、图片、PDF、DOCX）、设置页
- **THEN** 页面功能正常，控制台没有 CSP 违规

### Requirement: Opening local paths cannot launch programs

`open-path` MUST 先把目标解析为真实路径（跟随符号链接），再用真实路径和允许目录的真实路径做包含判断；真实路径不在允许目录内时 MUST 拒绝。目标是可执行程序、脚本、应用包、安装包或快捷方式时，应用 MUST 只在访达或资源管理器中定位该文件，MUST NOT 直接打开。打开普通文档和目录的行为 MUST 保持不变。

#### Scenario: Symlink escaping the allowed roots is refused

- **GIVEN** 允许目录内有一个指向 `/Applications` 的符号链接
- **WHEN** 页面请求打开该符号链接下的文件
- **THEN** 请求被拒绝，返回错误信息

#### Scenario: Executable file is revealed, not executed

- **WHEN** 页面请求打开允许目录内的 `run.command`、`install.sh`、`Tool.app` 或 `setup.exe`
- **THEN** 访达或资源管理器定位并选中该文件，文件没有被运行

#### Scenario: Document opens normally

- **WHEN** 页面请求打开允许目录内的 `report.md` 或一个目录
- **THEN** 系统用默认程序打开该文档或目录
