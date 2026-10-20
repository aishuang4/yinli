# Spec Delta

## Purpose

规定后端在捕获异常和输出日志时必须遵守的可见性要求，保证线上和桌面端出现的异常能从日志定位到模块和调用栈，而不是被静默吞掉。

## ADDED Requirements

### Requirement: Broad exception handlers are never silent

后端源码中捕获 `Exception` 或 `BaseException` 的处理块 MUST 至少满足一项：重新抛出；以包含调用栈的方式写日志；把异常转成明确的对外错误结果（例如 HTTP 错误响应或工具失败结果）并写 debug 以上级别日志。`except ...: pass` 与 `except ...: continue` MUST NOT 出现，除非该行带有写明理由的豁免注释。开发者能确定具体异常类型时 MUST 捕获具体类型。仓库 MUST 用静态检查强制这一点。

#### Scenario: Silent swallow is rejected

- **WHEN** 拉取请求新增 `try: ... except Exception: pass` 且没有豁免注释
- **THEN** Ruff 步骤失败，输出 `S110` 或 `BLE001`

#### Scenario: Unexpected trace write failure is visible

- **GIVEN** 运行追踪写入时发生意外异常
- **WHEN** 对话继续执行
- **THEN** 对话不中断，后端日志出现一条包含模块名和调用栈的记录

### Requirement: Backend logging is configured once with a readable format

后端 MUST 在创建应用前完成一次日志配置：项目日志输出到标准错误，每条包含时间、级别、模块名和消息；级别可通过配置调整，默认 INFO。重复配置 MUST NOT 产生重复输出。后端启动和生命周期代码 MUST 使用日志而不是 `print`。日志配置 MUST NOT 改变 uvicorn 自身访问日志的行为。

#### Scenario: Module log line has a timestamp and module name

- **WHEN** 后端任一模块用 `logging.getLogger(__name__).warning("x")` 写日志
- **THEN** 标准错误出现一行，包含时间、`WARNING`、模块名和 `x`，且只出现一次

#### Scenario: Startup messages go through logging

- **WHEN** 后端启动
- **THEN** 启动信息以 INFO 日志输出，`src/server/main.py` 中没有 `print(` 调用
