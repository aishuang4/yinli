# Spec Delta

## Purpose

规定后端处理 HTTP 请求时如何避免阻塞单个事件循环，使一个慢请求不会拖住流式回复、SSE 通知和其他并发请求，并在发生卡顿时留下可排查的记录。

## ADDED Requirements

### Requirement: Blocking route work runs off the event loop

没有任何 `await`、`async for`、`async with` 的路由处理函数 MUST 声明为同步函数（`def`），由框架放入线程池执行；例外只有必须在事件循环线程内调用循环相关 API 的路由，它们 MUST 列在仓库内显式的保留清单里。仍为 `async` 的路由在调用同步数据库或文件系统操作时，MUST 通过线程池执行这些操作。仓库 MUST 有自动化测试，在新增不合规的 `async` 路由时失败。

#### Scenario: Slow storage call does not stall other requests

- **GIVEN** 某个已改为同步的路由在处理中耗时 1 秒（测试中用 `time.sleep(1)` 模拟慢查询）
- **WHEN** 它执行期间另一个客户端请求 `GET /api/health`
- **THEN** 健康检查在 200 ms 内返回

#### Scenario: New async route without await is rejected by tests

- **WHEN** 开发者新增一个函数体内没有 `await` 的 `async def` 路由，且未加入保留清单
- **THEN** 路由并发契约测试失败，并输出该路由的文件与函数名

#### Scenario: Stale keep-list entries are rejected

- **WHEN** 保留清单里的某个路由已被删除、已改为 `def`，或已包含 `await`
- **THEN** 契约测试失败，提示从清单移除该条目

### Requirement: Worker-thread code does not touch the event loop directly

在线程池中执行的路由代码 MUST NOT 直接调用与事件循环绑定、且不保证线程安全的 API，包括创建任务、取消任务、设置 Future 结果、向 asyncio 队列放入数据、设置 asyncio 事件。后端全量测试 MUST 能在 asyncio 调试模式下通过，且输出中不出现 `Non-thread-safe operation` 错误。

#### Scenario: Debug-mode test run catches cross-thread loop calls

- **WHEN** 以 `PYTHONASYNCIODEBUG=1` 运行后端全量测试
- **THEN** 测试通过，输出中没有 `Non-thread-safe operation invoked on an event loop other than the current one`

#### Scenario: Interrupting a group studio still works

- **GIVEN** 一个群聊工作室正在运行
- **WHEN** 用户调用中断、暂停或关闭接口
- **THEN** 运行中的任务被取消，状态与改造前一致

### Requirement: Hosted session authentication does not block the event loop

托管模式下，每个受保护请求的登录会话查询、租户状态查询和首次租户初始化 MUST 在线程池中执行。租户上下文 MUST 在请求自己的异步上下文中绑定，请求结束后 MUST 恢复。响应状态码和文案（401 需要登录、403 账号已停用）MUST 保持不变。

#### Scenario: Tenant context is visible to the route

- **GIVEN** 托管模式下一个已登录用户
- **WHEN** 用户请求一个同步路由和一个异步路由
- **THEN** 两个路由读到的当前租户都是该用户的租户

#### Scenario: Disabled account is still rejected

- **GIVEN** 一个租户状态不是 active 的用户持有有效会话
- **WHEN** 该用户请求受保护接口
- **THEN** 后端返回 403，`detail` 为 `账号已停用`

### Requirement: Background tasks are retained until they finish

后端创建的不被等待的后台任务 MUST 保存强引用，直到任务结束后再释放，避免任务在完成前被垃圾回收。后台任务抛出的异常 MUST 写入日志。

#### Scenario: Skill evolution task survives garbage collection

- **WHEN** 对话收尾时调度了技能自进化后台任务，随后立即执行 `gc.collect()`
- **THEN** 该任务仍在运行任务集合中，结束后从集合中移除

### Requirement: Event loop stalls are logged

后端 MUST 在启动后持续测量事件循环延迟。延迟达到配置阈值（默认 250 ms）时 MUST 写一条 warning，内容包含延迟毫秒数；10 秒内 MUST 最多写一条。阈值配置为 0 时 MUST 关闭监测。后端关闭时 MUST 停止监测任务。

#### Scenario: A blocking call produces one warning

- **GIVEN** 监测阈值为 250 ms
- **WHEN** 事件循环内发生一次 600 ms 的同步阻塞
- **THEN** 日志中出现一条包含延迟毫秒数的 warning

#### Scenario: Monitor can be disabled

- **GIVEN** 阈值配置为 0
- **WHEN** 后端启动
- **THEN** 不创建监测任务
