# Design

## Context

本设计基于 2026-10-20 09:03 的工作区快照（HEAD `c534c3f` 加未提交改动）。下文的行号都是"该快照里的位置"，实施时以符号名为准。

现状：

- 桌面后端的本机边界只有 `backend/src/server/local_access.py` 的 `LocalAccessMiddleware`：对端不是回环地址（或 TestClient 的 `testclient`）就返回 403。它不看 Host、Origin，也没有令牌。
- `backend/src/server/main.py` 约 141–153 行的中间件注册顺序是 `HostedSessionMiddleware` → `LocalAccessMiddleware` → `ProductFeatureGateMiddleware` → `CORSMiddleware`。Starlette 中后注册的在外层先执行，所以实际执行顺序是 CORS → ProductFeatureGate → LocalAccess → HostedSession → 路由。
- `electron/main.ts` 约 374–386 行拉起后端时传 `AGENT_WORKSTATION_CORS_ORIGINS='["http://localhost:5173","http://localhost:3000","null","file://"]'`。`null` 来源被允许，意味着任何网页在沙箱 iframe 里发起的请求都能读到响应。打包版端口在 39100–39299 之间，开发版固定 8765，枚举成本很低。
- 前端对后端的访问有三类：经 `frontend/src/utils/apiUrl.ts` 全局 fetch 桥的 fetch；`hooks/useNotifications.ts`、`hooks/useWorkflowRuns.ts` 里的 `EventSource`；`features/file-preview/FilePreviewPane.tsx` 里的 `<img>`、`<iframe>`、下载链接。后两类无法在脚本里加请求头。
- Electron 主进程自己访问后端的地方：`GET /api/health`（约 233 行用于识别旧实例、约 557 行用于等待就绪）和 `refreshTrustedProjectRootsFromBackend`（约 851–858 行读 `/api/workspaces`）。
- 托管验证码：`backend/src/server/auth/secrets.py` 的 `new_code()` 生成 6 位数字；`backend/src/storage/auth_records.py` 的 `save_email_code` 只插入不作废，`consume_email_code` 按 `code_hash` 直接查表，错误不计数；`CODE_TTL` 15 分钟。托管部署是 Caddy 反代到 `uvicorn --workers 1 --no-proxy-headers`（`deploy/Dockerfile`），所以后端看到的对端永远是 `127.0.0.1`，真实客户端在 Caddy 写入的 `X-Forwarded-For` 里。
- Electron 主窗口 `setWindowOpenHandler` 对任意地址调用 `shell.openExternal`；没有 `will-navigate` 拦截；`frontend/index.html` 没有 CSP；`resolveSafeLocalPath`（约 909–940 行）不解析符号链接，`open-path`（约 681 行）对任何文件都调用 `shell.openPath`。前端没有路由库，页面切换用 `history.replaceState` 改 query。

## Goals / Non-Goals

**Goals:**

- 任意网页（包括沙箱 iframe 和 DNS 重绑定场景）都不能读写打包版桌面后端。
- 开发模式不增加任何配置步骤。
- 托管验证码无法被穷举，认证接口有基本限流。
- 聊天内容里的链接和路径不能启动本机程序或把主窗口导航到外部页面。

**Non-Goals:**

- 不防御同一系统用户下的恶意本机进程（它本来就能读 userData 和进程环境变量）。
- 不按邮箱做登录锁定，避免攻击者用错误密码锁死他人账号；密码穷举由 argon2 和按客户端限流共同抑制。
- 不加长验证码位数，不改验证码邮件内容。
- 不改 Electron 可信目录的来源（由 `simplify-global-session-context` 19.3 负责）。
- 不改前端业务请求代码（统一请求客户端属于 `restructure-code-layering`）。
- 不把渲染进程改为自定义协议加载。

## Decisions

### 1. 新增纯 ASGI 中间件 `DesktopRequestGuardMiddleware`

新文件 `backend/src/server/desktop_guard.py`，写法与 `LocalAccessMiddleware` 一致（纯 ASGI，不用 `BaseHTTPMiddleware`，避免影响 SSE 流式响应）。判定逻辑抽成纯函数 `evaluate_desktop_request`，单测直接覆盖：

```python
"""桌面模式请求守卫：Host、Origin、启动令牌三道检查。托管模式直接放行。"""

from __future__ import annotations

import hmac

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from src.config.settings import settings
from src.tenancy.context import is_hosted_mode

DESKTOP_TOKEN_HEADER = "x-yinli-desktop-token"
_TOKEN_EXEMPT: frozenset[tuple[str, str]] = frozenset({("GET", "/api/health"), ("HEAD", "/api/health")})


def host_name(raw_host: str) -> str:
    """从 Host 头取主机名：去端口、去 IPv6 方括号、转小写、去末尾点。"""

    value = raw_host.strip().lower()
    if value.startswith("["):
        end = value.find("]")
        value = value[1:end] if end > 0 else ""
    elif value.count(":") == 1:
        value = value.rsplit(":", 1)[0]
    return value.rstrip(".")


def evaluate_desktop_request(*, method: str, path: str, headers: dict[str, str]) -> tuple[int, str] | None:
    """返回 None 表示放行；否则返回 (状态码, detail)。headers 的键必须是小写。"""

    if host_name(headers.get("host", "")) not in settings.desktop_allowed_hosts:
        return 403, "desktop_host_rejected"
    origin = headers.get("origin")
    if origin is not None and origin not in settings.cors_origins:
        return 403, "desktop_origin_rejected"
    expected = settings.desktop_access_token
    if expected and method != "OPTIONS" and (method, path) not in _TOKEN_EXEMPT:
        supplied = headers.get(DESKTOP_TOKEN_HEADER, "")
        if not hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8")):
            return 401, "desktop_token_required"
    return None


class DesktopRequestGuardMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket") or is_hosted_mode():
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []}
        rejected = evaluate_desktop_request(
            method=str(scope.get("method") or "GET").upper(),
            path=str(scope.get("path") or ""),
            headers=headers,
        )
        if rejected is None:
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        status, detail = rejected
        await JSONResponse({"detail": detail}, status_code=status)(scope, receive, send)
```

注册位置：在 `main.py` 中 `app.add_middleware(ProductFeatureGateMiddleware)` 之后、`app.add_middleware(CORSMiddleware, ...)` 之前插入 `app.add_middleware(DesktopRequestGuardMiddleware)`。执行顺序变为 CORS → DesktopRequestGuard → ProductFeatureGate → LocalAccess → HostedSession。CORS 在最外层，合法预检仍由它直接应答；守卫在 LocalAccess 之前，两者可以独立测试。`LocalAccessMiddleware` 保持不变。

选择理由：Host 校验挡 DNS 重绑定；Origin 校验挡普通跨站表单和 fetch；令牌挡 `null` 来源和一切伪造 Origin 的情况。只做前两项无法处理打包版必须允许的 `null`/`file://` 来源。

### 2. 配置项

在 `backend/src/config/settings.py` 的 `Settings` 类里、`cors_origins` 之后新增（`env_prefix` 已是 `AGENT_WORKSTATION_`）：

```python
    # 桌面内置后端的启动令牌。仅由 Electron 主进程在拉起后端时通过环境变量注入；为空表示开发模式，不校验令牌。
    desktop_access_token: str = ""
    # 桌面模式允许的 Host 主机名（不含端口）。
    desktop_allowed_hosts: list[str] = ["127.0.0.1", "localhost", "::1"]
```

`/api/auth/config` 等任何会输出配置的接口 MUST NOT 包含 `desktop_access_token`。`backend/test/conftest.py` 的 autouse fixture `local_tenant` 在现有 `monkeypatch.setattr("src.config.settings.settings.database_url", ...)` 之后增加两行，防止开发机 `.env` 污染测试，并让 TestClient 的默认 Host `testserver` 只在测试里被允许：

```python
    monkeypatch.setattr("src.config.settings.settings.desktop_access_token", "")
    monkeypatch.setattr("src.config.settings.settings.desktop_allowed_hosts", ["127.0.0.1", "localhost", "::1", "testserver"])
```

生产默认值里不包含 `testserver`。

### 3. Electron 生成令牌并在会话网络层注入

新文件 `electron/desktopAccess.ts`（纯函数，便于 vitest 覆盖）：

```ts
import { randomBytes } from 'crypto';

export const DESKTOP_TOKEN_HEADER = 'X-Yinli-Desktop-Token';

/** 每次应用启动生成一次：32 字节随机数，base64url 编码。 */
export function createDesktopAccessToken(): string {
  return randomBytes(32).toString('base64url');
}

/** 只有目标 origin 与内置后端 origin 完全相同时才附加令牌。 */
export function shouldAttachDesktopToken(requestUrl: string, backendOrigin: string): boolean {
  try {
    return new URL(requestUrl).origin === new URL(backendOrigin).origin;
  } catch {
    return false;
  }
}
```

`electron/main.ts` 改动：

1. 把第 11 行 `import { app, BrowserWindow, dialog, ipcMain, Menu, Notification, screen, shell } from 'electron';` 增加 `session`；导入 `createDesktopAccessToken`、`shouldAttachDesktopToken`、`DESKTOP_TOKEN_HEADER`。
2. 模块顶层（`isDev` 定义之后）新增 `const desktopAccessToken = createDesktopAccessToken();`。后端崩溃重启时复用同一个值；应用重启即新值。
3. 拉起后端的 `env` 对象（约 374–386 行）在 `AGENT_WORKSTATION_CORS_ORIGINS` 之后增加 `AGENT_WORKSTATION_DESKTOP_ACCESS_TOKEN: desktopAccessToken,`。
4. 在 `app.whenReady()` 回调中、`createWindow()` 之前安装注入器。不传 URL 过滤器，在回调里用 `shouldAttachDesktopToken` 精确比较 origin，避免依赖 match pattern 对端口的语义。每个 session 只能有一个 `onBeforeSendHeaders` 监听，当前代码没有其他监听：

```ts
session.defaultSession.webRequest.onBeforeSendHeaders((details, callback) => {
  if (!isDev && shouldAttachDesktopToken(details.url, backendServerUrl())) {
    details.requestHeaders[DESKTOP_TOKEN_HEADER] = desktopAccessToken;
  }
  callback({ requestHeaders: details.requestHeaders });
});
```

5. 新增主进程专用的 `backendFetch`，`refreshTrustedProjectRootsFromBackend` 里的两处 `fetch(`${backendServerUrl()}/api/...`)` 改为调用它。两处 `/api/health` 保持原样（健康检查免令牌，且约 233 行访问的是上一轮实例，不知道它的令牌）：

```ts
async function backendFetch(pathname: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  if (!isDev) {
    headers.set(DESKTOP_TOKEN_HEADER, desktopAccessToken);
  }
  return fetch(`${backendServerUrl()}${pathname}`, { ...init, headers });
}
```

6. preload 和 IPC 不新增任何返回令牌的方法。

选择理由：会话网络层注入同时覆盖 fetch、EventSource、`<img>`、`<iframe>` 和下载，前端一行不改，令牌也不进入渲染进程。备选方案是把令牌放进 query 参数或通过 preload 交给前端，前者会把令牌写进 uvicorn 访问日志和 DOM，后者需要改 EventSource 和所有资源 URL，均不采用。

开发模式：Electron 不拉起后端，后端由 `scripts/dev.sh` 或 `scripts/electron-dev.sh` 启动，未配置令牌；前端走 Vite 代理（`changeOrigin: true`，Host 为 `localhost:8765`），Host 和 Origin 校验都能通过。

### 4. 外链与导航策略抽成 `electron/navigationPolicy.ts`

```ts
const WEB_SCHEMES = new Set(['http:', 'https:', 'mailto:']);

/** 外链白名单：网页、邮件，以及原有的 obsidian://open。 */
export function isAllowedExternalUrl(value: string): boolean {
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return false;
  }
  if (WEB_SCHEMES.has(url.protocol)) {
    return true;
  }
  return url.protocol === 'obsidian:' && url.hostname === 'open';
}

/** 去掉 query 和 hash 后与应用入口相同才算应用内导航。 */
export function isAppEntryUrl(target: string, entryUrl: string): boolean {
  try {
    const a = new URL(target);
    const b = new URL(entryUrl);
    if (a.protocol === 'file:' || b.protocol === 'file:') {
      return a.protocol === b.protocol && decodeURIComponent(a.pathname) === decodeURIComponent(b.pathname);
    }
    return a.origin === b.origin;
  } catch {
    return false;
  }
}

/** 日志只记协议名，不记完整地址。 */
export function schemeForLog(value: string): string {
  try {
    return new URL(value).protocol;
  } catch {
    return 'invalid';
  }
}
```

`electron/main.ts` 改动：

1. 删除本文件内的 `isAllowedExternalUrl`（约 942–949 行），改为从 `navigationPolicy` 导入。IPC `open-external` 的原有判断语句不变，因此它会同时放行 http/https/mailto。
2. 新增 `function appEntryUrl(): string { return isDev ? DEV_SERVER_URL : pathToFileURL(path.join(__dirname, '../dist/index.html')).href; }`（在文件顶部增加 `import { pathToFileURL } from 'url';`）。
3. `setWindowOpenHandler` 改为：

```ts
mainWindow.webContents.setWindowOpenHandler(({ url }) => {
  if (isAllowedExternalUrl(url)) {
    void shell.openExternal(url);
  } else {
    console.warn(`[shell] blocked window.open scheme=${schemeForLog(url)}`);
  }
  return { action: 'deny' };
});
```

4. 紧接其后增加导航拦截，`will-navigate` 和 `will-redirect` 共用同一个处理函数：

```ts
const guardNavigation = (event: Electron.Event, url: string) => {
  if (isAppEntryUrl(url, appEntryUrl())) {
    return;
  }
  event.preventDefault();
  if (isAllowedExternalUrl(url)) {
    void shell.openExternal(url);
  } else {
    console.warn(`[shell] blocked navigation scheme=${schemeForLog(url)}`);
  }
};
mainWindow.webContents.on('will-navigate', guardNavigation);
mainWindow.webContents.on('will-redirect', guardNavigation);
```

5. 在 `app.whenReady()` 之前注册全局兜底，对以后任何新建的 webContents 一律拒绝弹窗和 `<webview>`：

```ts
app.on('web-contents-created', (_event, contents) => {
  contents.on('will-attach-webview', (event) => event.preventDefault());
  if (contents !== mainWindow?.webContents) {
    contents.setWindowOpenHandler(() => ({ action: 'deny' }));
  }
});
```

`history.replaceState`/`pushState` 属于同文档导航，不会触发 `will-navigate`；刷新和 Vite 的整页重载目标是入口本身，会被 `isAppEntryUrl` 放行。

### 5. 打包版 CSP 由 Vite 插件在构建时注入

在 `frontend/vite.config.ts` 中新增插件并加入 `plugins`。只在 `vite build` 时生效（`apply: 'build'`），开发服务器不注入，保证 React Refresh 的内联前导脚本可用。托管 Web 与桌面共用同一份构建，托管下 `/api` 与页面同源，`'self'` 已覆盖。

```ts
const PACKAGED_CSP = [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob: http://127.0.0.1:*",
  "media-src 'self' blob: http://127.0.0.1:*",
  "font-src 'self' data:",
  "connect-src 'self' http://127.0.0.1:*",
  "frame-src 'self' blob: http://127.0.0.1:*",
  "worker-src 'self' blob:",
  "object-src 'none'",
  "base-uri 'none'",
  "form-action 'none'",
].join('; ');

function packagedCspPlugin(): Plugin {
  return {
    name: 'yinli-packaged-csp',
    apply: 'build',
    transformIndexHtml: {
      order: 'post',
      handler: (html) =>
        html.replace('<head>', `<head>\n    <meta http-equiv="Content-Security-Policy" content="${PACKAGED_CSP}" />`),
    },
  };
}
```

`style-src` 保留 `'unsafe-inline'`，因为 antd 的 CSS-in-JS 会在运行时插入 `<style>`。验证规则：打包后在 DevTools 控制台逐页检查（见 tasks 4.3）。若发现 file:// 页面下 `'self'` 不匹配本地资源，只允许在 `script-src`、`style-src`、`img-src`、`font-src` 里追加 `file:`，并把追加原因写进插件上方的注释；不允许追加 `'unsafe-inline'` 或 `'unsafe-eval'` 到 `script-src`。

### 6. `open-path` 解析真实路径并拦截可执行文件

在 `electron/navigationPolicy.ts` 中新增（与上文同一文件，都是纯函数）：

```ts
import * as path from 'path';

const REVEAL_ONLY_EXTENSIONS = new Set([
  '.app', '.command', '.tool', '.terminal', '.workflow', '.action', '.scpt', '.applescript',
  '.pkg', '.mpkg', '.dmg', '.sh', '.bash', '.zsh', '.fish', '.csh', '.py', '.pyw', '.rb', '.pl',
  '.php', '.js', '.mjs', '.cjs', '.jar', '.exe', '.com', '.bat', '.cmd', '.ps1', '.psm1', '.vbs',
  '.vbe', '.wsf', '.wsh', '.msi', '.msp', '.scr', '.lnk', '.url', '.webloc', '.inetloc', '.fileloc',
  '.desktop', '.appimage', '.run', '.reg', '.cpl', '.hta', '.jse',
]);

export interface PathKind {
  isFile: boolean;
  isDirectory: boolean;
  mode: number;
}

/** 可执行、脚本、应用包、安装包、快捷方式只在文件管理器中定位。 */
export function shouldRevealInsteadOfOpen(realPath: string, kind: PathKind, platform: NodeJS.Platform): boolean {
  const ext = path.extname(realPath).toLowerCase();
  if (kind.isDirectory) {
    return ext === '.app';
  }
  if (!kind.isFile) {
    return true;
  }
  if (REVEAL_ONLY_EXTENSIONS.has(ext)) {
    return true;
  }
  return platform !== 'win32' && (kind.mode & 0o111) !== 0;
}
```

`electron/main.ts` 改动：

1. `resolveSafeLocalPath` 在找到存在的 `current` 后，不再直接返回，而是：

```ts
    if (fs.existsSync(current)) {
      const real = await fs.promises.realpath(current);
      const realRoots = await Promise.all(allowedRoots.map((root) => fs.promises.realpath(root).catch(() => root)));
      return realRoots.some((root) => isPathInsideRoot(real, root)) ? real : null;
    }
```

允许目录列表仍是现在的 cwd、userData、home 和 `trustedProjectRoots`，只是比较双方都换成真实路径（macOS 的 `/tmp` 会变为 `/private/tmp`）。`embed-file-preview` 也调用这个函数，自动获得同样的保护。

2. `open-path` 处理函数在 `resolveSafeLocalPath` 成功后、`shell.openPath` 之前增加：

```ts
  const stat = await fs.promises.stat(resolvedPath);
  if (shouldRevealInsteadOfOpen(resolvedPath, { isFile: stat.isFile(), isDirectory: stat.isDirectory(), mode: stat.mode }, process.platform)) {
    shell.showItemInFolder(resolvedPath);
    return { ok: true, revealed: true };
  }
```

返回值新增可选字段 `revealed`，前端现有调用方只看 `ok` 和 `error`，不需要改；`frontend/src/types/electron.d.ts` 中 `openPath` 的返回类型同步增加 `revealed?: boolean`。

### 7. 验证码：作废旧码、持久化错误次数、按邮箱限频

表结构：`backend/src/storage/auth_schema.py` 的 `auth_email_codes` 在 `used_at TEXT,` 之后增加 `failed_attempts INTEGER NOT NULL DEFAULT 0,`。旧库补列：`backend/src/storage/control_db.py` 的 `ensure_control_db` 在 `conn.executescript(CONTROL_SCHEMA_SQL)` 之后调用新的私有函数：

```python
def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    """旧控制面库幂等补列；CREATE TABLE IF NOT EXISTS 不会给已存在的表加列。"""

    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

# ensure_control_db 内：
            _ensure_column(conn, "auth_email_codes", "failed_attempts", "INTEGER NOT NULL DEFAULT 0")
```

`backend/src/storage/auth_records.py` 改动（常量放在 `CODE_TTL` 旁边）：

```python
MAX_CODE_ATTEMPTS = 5
CODE_MIN_INTERVAL = timedelta(seconds=60)
CODE_HOURLY_LIMIT = 5


def may_issue_email_code(*, email: str, purpose: str) -> bool:
    """同一邮箱同一用途：60 秒内最多 1 次，1 小时内最多 5 次。"""

    control_db.ensure_control_db()
    now = _now()
    with control_db._control_lock, control_db.control_connect() as conn:
        rows = conn.execute(
            "SELECT created_at FROM auth_email_codes WHERE email = ? AND purpose = ? AND created_at >= ?",
            (email, purpose, _iso(now - timedelta(hours=1))),
        ).fetchall()
    created = [ts for ts in (_parse(row["created_at"]) for row in rows) if ts is not None]
    if len(created) >= CODE_HOURLY_LIMIT:
        return False
    return not any(now - ts < CODE_MIN_INTERVAL for ts in created)
```

`save_email_code` 在 INSERT 之前、同一个 `with` 块内先执行：

```python
        conn.execute(
            "UPDATE auth_email_codes SET used_at = ? WHERE email = ? AND purpose = ? AND used_at IS NULL",
            (database._utc_now(), email, purpose),
        )
```

`consume_email_code` 整体改为"取该邮箱该用途最新的一个可用码，再比较摘要"，不再按 `code_hash` 查表（否则无法给错误计数）：

```python
def consume_email_code(*, email: str, purpose: str, raw_code: str) -> bool:
    """核销未过期验证码。错误累计 MAX_CODE_ATTEMPTS 次即作废。错误、过期、已用、已作废都返回 False。"""

    control_db.ensure_control_db()
    now = _now()
    digest = hash_secret(raw_code)
    with control_db._control_lock, control_db.control_connect() as conn:
        row = conn.execute(
            """
            SELECT id, code_hash, expires_at, failed_attempts FROM auth_email_codes
            WHERE email = ? AND purpose = ? AND used_at IS NULL
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (email, purpose),
        ).fetchone()
        if row is None:
            return False
        expires_at = _parse(row["expires_at"])
        if expires_at is None or expires_at < now:
            return False
        if hmac.compare_digest(str(row["code_hash"]), digest):
            conn.execute("UPDATE auth_email_codes SET used_at = ? WHERE id = ?", (_iso(now), row["id"]))
            return True
        attempts = int(row["failed_attempts"] or 0) + 1
        conn.execute(
            "UPDATE auth_email_codes SET failed_attempts = ?, used_at = ? WHERE id = ?",
            (attempts, _iso(now) if attempts >= MAX_CODE_ATTEMPTS else None, row["id"]),
        )
        return False
```

"已作废"复用 `used_at` 列表示，不新增状态列；对调用方而言作废与已用一致。本函数原有的其余行为（过期返回 False）不变。`hmac` 需要在文件顶部导入。

`backend/src/server/auth/service.py`：在 `register_user`（约 61 行 `code = new_code()`）和 `request_password_reset`（约 124 行 `code = new_code()`）之前各增加一行 `if not auth_records.may_issue_email_code(email=normalized, purpose="register"): return`（重置处 `purpose="reset"`），让路由照常返回 `{"ok": True}`。用途字符串沿用现有的 `register`/`reset`。注册流程里新用户首次注册没有历史码，不会被拦；这条主要保护忘记密码。

### 8. 认证接口按客户端限流：进程内滑动窗口

新文件 `backend/src/server/auth/rate_limit.py`：

```python
"""托管认证接口的进程内滑动窗口限流。部署是单 worker，进程内状态即全局状态。"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from collections.abc import Callable

from fastapi import HTTPException, Request

from src.tenancy.context import is_hosted_mode

_LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})


class SlidingWindowLimiter:
    def __init__(self, *, limit: int, window_seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.limit = limit
        self.window = window_seconds
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str) -> float | None:
        """记录一次调用。未超限返回 None；超限返回需要等待的秒数，且不记录本次。"""

        now = self._clock()
        with self._lock:
            bucket = self._hits.setdefault(key, deque())
            while bucket and now - bucket[0] >= self.window:
                bucket.popleft()
            if len(bucket) >= self.limit:
                return self.window - (now - bucket[0])
            bucket.append(now)
            return None

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


LIMITERS: dict[str, SlidingWindowLimiter] = {
    "login": SlidingWindowLimiter(limit=10, window_seconds=60),
    "verify": SlidingWindowLimiter(limit=10, window_seconds=60),
    "reset-password": SlidingWindowLimiter(limit=10, window_seconds=60),
    "register": SlidingWindowLimiter(limit=5, window_seconds=60),
    "forgot-password": SlidingWindowLimiter(limit=5, window_seconds=60),
}


def client_key(request: Request) -> str:
    """Caddy 反代时取 X-Forwarded-For 最后一跳（Caddy 追加的真实对端），否则取直连对端。"""

    peer = request.client.host if request.client else ""
    forwarded = request.headers.get("x-forwarded-for", "")
    if peer in _LOOPBACK and forwarded:
        last = forwarded.split(",")[-1].strip()
        if last:
            return last
    return peer or "unknown"


def rate_limited(name: str) -> Callable[[Request], None]:
    limiter = LIMITERS[name]

    def dependency(request: Request) -> None:
        if not is_hosted_mode():
            return
        retry_after = limiter.hit(client_key(request))
        if retry_after is not None:
            raise HTTPException(
                status_code=429,
                detail="请求过于频繁，请稍后再试",
                headers={"Retry-After": str(max(1, math.ceil(retry_after)))},
            )

    return dependency


def reset_all_limiters() -> None:
    """测试用：清空全部计数。"""

    for limiter in LIMITERS.values():
        limiter.reset()
```

`backend/src/server/auth/routes.py`：五个路由的 `dependencies=_HOSTED` 改为 `dependencies=[*_HOSTED, Depends(rate_limited("<name>"))]`，`<name>` 与路径末段一致（`register`、`verify`、`login`、`forgot-password`、`reset-password`）。`_HOSTED` 里的依赖先执行，桌面模式下路由本身先被 `_HOSTED` 拒绝，不会计数。`backend/test/conftest.py` 的 `local_tenant` fixture 在 `yield` 前后各调用一次 `rate_limit.reset_all_limiters()`。

选择理由：部署是单 worker，进程内计数就是全局计数；重启后清零可以接受，因为真正关键的"单码错误次数"已持久化在数据库里。不引入 Redis 或第三方限流库。计数器用 `threading.Lock`，因为 `fix-runtime-correctness-and-quality-gates` 会把这些路由改成 `def`，在线程池里并发执行。

## Risks / Trade-offs

- [绕过 Electron 调用打包版后端的本机脚本会拿到 401] → 这是预期行为，写入 proposal 的 BREAKING；仓库内脚本都走开发后端，不受影响。
- [令牌放在后端进程环境变量里，同用户进程可读] → 属于 Non-Goals 已列明的威胁范围外。
- [会话层注入头导致跨源请求多一次预检] → Chromium 在网络层修改请求头时不重新计算 CORS 预检；即使预检，后端 `allow_headers=["*"]` 也会放行。tasks 2.5 用打包版实测 SSE、图片、下载。
- [CSP 误伤某个预览组件] → Decision 5 给出了唯一允许的放宽方式和记录要求；实在不行可单独回滚这一个插件。
- [可执行扩展名名单会让 `.py`、`.js` 等代码文件从"用编辑器打开"变为"在访达中定位"] → macOS 的 Python Launcher 和 Windows 的脚本宿主会直接执行这些文件，安全优先；用户仍可在访达里自行打开。
- [限流以 IP 为键，同一出口 IP 的多个用户会互相影响] → 阈值按人工操作频率留了余量；如有需要以后改为配置项。

## Migration Plan

1. 后端先合入守卫与配置（未配置令牌时只多 Host/Origin 检查，开发环境无感）。
2. Electron 合入令牌生成、注入和 `backendFetch`，打包验证。
3. Electron 合入外链/导航/`open-path` 策略，前端合入 CSP 插件，打包验证。
4. 托管侧合入验证码与限流；旧控制面库在下次启动时自动补列，无需停机迁移。

### Rollback

- 桌面守卫：删除 `main.py` 中的 `add_middleware(DesktopRequestGuardMiddleware)` 一行即完全恢复旧行为；Electron 继续附加令牌头无副作用。
- CSP：从 `plugins` 中移除 `packagedCspPlugin()`。
- 验证码：回退 `auth_records.py` 和 `service.py` 即可，`failed_attempts` 列保留不影响旧代码。
- 限流：把五个路由的 `dependencies` 改回 `_HOSTED`。

## Open Questions

无。阈值（5 次、60 秒、1 小时 5 次、60 秒 10/5 次）已作为本变更的定值，调整需要同步修改 spec。
