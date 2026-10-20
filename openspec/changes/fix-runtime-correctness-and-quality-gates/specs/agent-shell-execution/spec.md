# Spec Delta

## Purpose

规定智能体通过命令工具在 Windows 本机执行命令时的环境变量、执行次数、超时处理和执行后端选择，保证 Windows 与 macOS 上的智能体看到同样的工具链环境，并且命令不会被重复执行。

## ADDED Requirements

### Requirement: Windows local commands use the agent environment

在 Windows 上使用本机后端执行智能体命令时，子进程 MUST 使用与 macOS 本机执行相同来源的智能体环境变量（包括智能体虚拟环境与 PATH 调整），工作目录 MUST 是工具配置的工作目录，且 MUST NOT 弹出控制台窗口。

#### Scenario: Command sees the agent PATH

- **GIVEN** Windows 本机后端，智能体环境把智能体虚拟环境的 Scripts 目录放在 PATH 最前
- **WHEN** 智能体执行 `echo %PATH%` 和 `echo %VIRTUAL_ENV%`
- **THEN** PATH 以智能体虚拟环境的 Scripts 目录开头，VIRTUAL_ENV 为该虚拟环境根目录

### Requirement: Each command runs exactly once

一次命令工具调用 MUST 只创建一个命令解释器进程。实现 MUST NOT 通过捕获异常后换参数重试的方式再次执行同一命令。解释器无法启动时 MUST 返回退出码 127 和系统错误信息。

#### Scenario: Side effect happens once

- **WHEN** 智能体在 Windows 上执行 `echo x>> count.txt`
- **THEN** `count.txt` 只增加一行

#### Scenario: Missing shell reports a normal failure

- **GIVEN** 解析出的命令解释器路径不存在
- **WHEN** 智能体执行任意命令
- **THEN** 工具返回退出码 127 的失败结果，不抛出未处理异常

### Requirement: Timed-out commands stop their whole process tree

Windows 本机命令超过超时时间时，实现 MUST 结束命令解释器及其所有子进程，然后返回超时结果（退出码 -1，错误输出 `timed out`）。工具向智能体展示的超时文案 MUST 与现有文案一致。

#### Scenario: Long-running child is terminated

- **GIVEN** 超时时间为 1 秒
- **WHEN** 智能体执行一个启动子进程并等待 30 秒的命令
- **THEN** 约 1 秒后工具返回超时，该子进程已不存在

### Requirement: Non-local backends keep their own execution on Windows

当命令工具绑定的是非本机执行后端（例如沙箱后端）时，即使宿主是 Windows，实现也 MUST 使用该后端的标准命令执行流程，MUST NOT 套用 Windows `cmd.exe` 包装。

#### Scenario: Sandbox backend on a Windows host

- **GIVEN** Windows 宿主，命令工具绑定沙箱后端
- **WHEN** 智能体执行 `ls`
- **THEN** 命令由沙箱后端按其默认方式执行，参数里没有 `cmd.exe`
