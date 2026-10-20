# Spec Delta

## Purpose

前端只有一个访问后端的入口 `frontend/src/api/client.ts`：它负责解析桌面端后端地址、判断响应状态、把失败响应转换为统一的 `ApiError`。业务代码不再各自写 `fetch` 与 `!res.ok` 分支，也不再依赖改写全局 `window.fetch`。

## ADDED Requirements

### Requirement: All backend requests go through the API client

`frontend/src/` 中除 `api/client.ts` 与 `*.test.ts(x)` 外，任何文件 MUST NOT 调用裸 `fetch(`。业务代码 MUST 使用 `apiFetch`（需要原始 `Response`，如 SSE 流、下载）、`apiJson<T>`（JSON 响应）或 `apiVoid`（无响应体）。

#### Scenario: Raw fetch is rejected

- **GIVEN** 某组件新增 `await fetch('/api/agents')`
- **WHEN** 执行 `npm --prefix frontend run test`
- **THEN** `src/api/noRawFetch.test.ts` 失败并列出该文件与行号

### Requirement: Failed responses raise ApiError

当响应状态不在 200–299 时，`apiFetch`、`apiJson`、`apiVoid` SHALL 抛出 `ApiError`。`ApiError` MUST 包含 `status`（HTTP 状态码）、`code`（响应体 `detail.code`，或 `detail` 为字符串时即该字符串，否则为空串）、`message`（`detail.message`、字符串 `detail` 或调用方传入的 fallback，按此顺序取第一个非空值）与 `detail`（原始值）。网络层异常（`TypeError`）与 `AbortError` MUST 原样抛出，不包装。

#### Scenario: Structured error detail

- **WHEN** 后端返回 409 与 `{"detail": {"code": "directory_outside_roots", "message": "目录不在允许范围"}}`
- **THEN** `apiJson` 抛出 `ApiError`，`status === 409`、`code === 'directory_outside_roots'`、`message === '目录不在允许范围'`

#### Scenario: Plain string detail

- **WHEN** 后端返回 400 与 `{"detail": "approval_batch_incomplete"}`
- **THEN** `ApiError.code === 'approval_batch_incomplete'` 且 `ApiError.message === 'approval_batch_incomplete'`；群聊工作室经 `groupStudioJson` 抛出的错误文案（`friendlyGroupStudioError(error.detail, fallback)`）与改动前逐字相同

#### Scenario: Non-JSON error body

- **WHEN** 后端返回 502 与 HTML 正文
- **THEN** `ApiError.message` 等于调用方传入的 fallback，`code === ''`

### Requirement: Desktop requests resolve the backend origin without patching fetch

`apiFetch` MUST 对每个以 `/api` 开头的路径调用 `resolveApiUrl`。`frontend/src/utils/apiUrl.ts` MUST NOT 再导出 `installDesktopFetchBridge`，`frontend/src/main.tsx` MUST NOT 改写 `window.fetch`。

#### Scenario: Electron production request

- **GIVEN** 页面由 `file://` 加载且 `window.electronAPI` 存在，桌面后端地址为 `http://127.0.0.1:8765`
- **WHEN** 调用 `apiJson('/api/agents')`
- **THEN** 实际请求地址为 `http://127.0.0.1:8765/api/agents`

#### Scenario: Browser development request

- **GIVEN** `window.electronAPI` 不存在
- **WHEN** 调用 `apiJson('/api/agents')`
- **THEN** 请求地址保持 `/api/agents`，由 Vite 代理转发
