"""带黑名单和超时的本地 Shell 工具。

Agent 工厂只负责装配工具，不实现命令解释器细节。
"""

from __future__ import annotations

import asyncio
import codecs
import os
import shutil
import signal
from collections.abc import AsyncGenerator, Callable, Mapping

from agentscope.message import TextBlock
from agentscope.permission import PermissionBehavior, PermissionContext, PermissionDecision
from agentscope.tool import Bash, ToolChunk
from agentscope.message import ToolResultState

from src.runtime.agent_python import agent_shell_env


class GuardedBash(Bash):
    """带提示级黑名单与 Windows 原生命令解释器支持的 Shell 工具。

    黑名单用于拦截明显误操作，不是可抵抗命令混淆的安全沙箱；强隔离依赖
    DockerWorkspace 与权限审批。
    """

    description: str = """Executes a local shell command and returns its output.

On Windows this tool uses cmd.exe directly. Use Windows command syntax there:
`dir`, `type`, `%PATH%`, `cd /d "C:\\path"`, and `&&`. Do not require Git
Bash to be installed. On macOS/Linux it uses the POSIX sh shell.
"""

    def __init__(
        self,
        shell_blacklist: list[str],
        timeout_seconds: int,
        cwd: str | None = None,
    ) -> None:
        super().__init__(cwd=cwd)
        self.shell_blacklist = [item.strip() for item in shell_blacklist if item.strip()]
        self.timeout_seconds = timeout_seconds

    async def _exec_windows_shell(self, shell_path: str, command: str, *, timeout_ms: int):
        payload = [shell_path, "/d", "/c", _windows_utf8_command(command)]
        try:
            return await self._backend.exec_shell(
                payload,
                cwd=self._cwd,
                timeout=timeout_ms / 1000.0,
                env=agent_shell_env(),
            )
        except TypeError:
            return await self._backend.exec_shell(
                payload,
                cwd=self._cwd,
                timeout=timeout_ms / 1000.0,
            )

    async def call(  # type: ignore[override]
        self,
        command: str,
        description: str = "",
        timeout: int = 120000,
    ) -> AsyncGenerator[ToolChunk, None]:
        """在 Windows 使用 UTF-8 编码的 ``cmd.exe``，避免 PATH 或 Git Bash 依赖。"""

        if os.name != "nt" and self._backend.__class__.__name__ == "LocalBackend":
            from src.tenancy.context import TenantMissing, current_tenant

            try:
                hosted_bash = current_tenant().is_hosted
            except TenantMissing:
                hosted_bash = False
            if hosted_bash:
                yield _bash_error_chunk(command, "Web 端命令只能在隔离沙箱中执行")
                return
            async for chunk in self._call_local_streaming(command, timeout):
                yield chunk
            return

        if os.name != "nt":
            async for chunk in super().call(command, description, timeout):
                yield chunk
            return

        timeout_ms = min(timeout, 600000)
        shell_path = _resolve_windows_command_shell()
        if not shell_path:
            yield _bash_error_chunk(
                command,
                "未找到 Windows 命令解释器 cmd.exe。请检查 COMSPEC 和 SystemRoot 环境变量。",
            )
            return

        try:
            result = await self._exec_windows_shell(
                shell_path,
                command,
                timeout_ms=timeout_ms,
            )
        except Exception as exc:
            detail = str(exc) or repr(exc) or exc.__class__.__name__
            yield _bash_error_chunk(
                command,
                f"无法启动 cmd.exe（{shell_path}，工作目录：{self._cwd or os.getcwd()}）：{detail}",
            )
            return

        stdout = _decode_windows_shell_output(result.stdout)
        stderr = _decode_windows_shell_output(result.stderr)
        if result.exit_code == -1 and result.stderr == b"timed out":
            yield _bash_error_chunk(command, f"命令在 {timeout_ms}ms 后超时。")
            return

        if not result.ok():
            detail = stderr or stdout or (
                f"cmd.exe 退出码为 {result.exit_code}，但未输出错误信息。"
            )
            yield _bash_error_chunk(command, detail, stdout=stdout)
            return

        output = stdout
        if stderr:
            output = f"{output}\n{stderr}" if output else stderr
        yield ToolChunk(
            content=[TextBlock(text=output[:30000])],
            state=ToolResultState.SUCCESS,
            is_last=True,
        )

    async def _call_local_streaming(
        self,
        command: str,
        timeout: int,
    ) -> AsyncGenerator[ToolChunk, None]:
        """本地 POSIX Bash 按可读 stdout 节奏返回，并在取消时终止进程组。"""

        timeout_ms = min(timeout, 600000)
        process = await asyncio.create_subprocess_exec(
            "/bin/sh",
            "-c",
            command,
            cwd=self._cwd,
            env=agent_shell_env(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        emitted_chars = 0
        output_truncated = False
        deadline = asyncio.get_running_loop().time() + timeout_ms / 1000.0
        try:
            assert process.stdout is not None
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise asyncio.TimeoutError
                raw = await asyncio.wait_for(process.stdout.read(4096), timeout=remaining)
                if not raw:
                    break
                text = decoder.decode(raw)
                visible = text[:max(0, 30000 - emitted_chars)]
                output_truncated = output_truncated or len(visible) < len(text)
                emitted_chars += len(visible)
                if visible:
                    yield ToolChunk(
                        content=[TextBlock(text=visible)],
                        state=ToolResultState.RUNNING,
                        is_last=False,
                    )
            tail = decoder.decode(b"", final=True)
            visible_tail = tail[:max(0, 30000 - emitted_chars)]
            output_truncated = output_truncated or len(visible_tail) < len(tail)
            if visible_tail:
                yield ToolChunk(
                    content=[TextBlock(text=visible_tail)],
                    state=ToolResultState.RUNNING,
                    is_last=False,
                )
            exit_code = await process.wait()
        except asyncio.TimeoutError:
            await _terminate_posix_process_group(process)
            yield _bash_error_chunk(command, f"命令在 {timeout_ms}ms 后超时。")
            return
        except asyncio.CancelledError:
            await _terminate_posix_process_group(process)
            raise

        if exit_code != 0:
            yield _bash_error_chunk(command, f"Shell 退出码为 {exit_code}。")
            return
        final_text = "\n... (output truncated)" if output_truncated else ""
        yield ToolChunk(
            content=[TextBlock(text=final_text)],
            state=ToolResultState.SUCCESS,
            is_last=True,
        )

    async def check_permissions(self, tool_input: dict, context: PermissionContext) -> PermissionDecision:
        command = str(tool_input.get("command") or "")
        for blocked in self.shell_blacklist:
            if blocked and blocked in command:
                return PermissionDecision(
                    behavior=PermissionBehavior.DENY,
                    message=f"Command blocked by Agent guardrails: {blocked}",
                    decision_reason="Agent shell blacklist matched",
                    bypass_immune=True,
                )

        timeout_ms = int(tool_input.get("timeout") or 0)
        max_timeout_ms = max(1, self.timeout_seconds) * 1000
        if timeout_ms > max_timeout_ms:
            tool_input["timeout"] = max_timeout_ms

        return await super().check_permissions(tool_input, context)


async def _terminate_posix_process_group(process: asyncio.subprocess.Process) -> None:
    """终止 Bash 及其派生子进程，避免 Run cancel 后命令继续执行。"""

    if process.returncode is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    await process.wait()


def _resolve_windows_command_shell(
    environ: Mapping[str, str] | None = None,
    *,
    path_exists: Callable[[str], bool] = os.path.isfile,
    command_lookup: Callable[[str], str | None] = shutil.which,
) -> str | None:
    """定位 Windows ``cmd.exe``，优先使用系统给出的绝对路径。"""

    values = environ if environ is not None else os.environ
    candidates = [
        values.get("ComSpec") or values.get("COMSPEC"),
        (
            os.path.join(values["SystemRoot"], "System32", "cmd.exe")
            if values.get("SystemRoot")
            else None
        ),
    ]
    for candidate in candidates:
        if candidate and path_exists(candidate):
            return candidate
    return command_lookup("cmd.exe") or command_lookup("cmd")


def _windows_utf8_command(command: str) -> str:
    """让当前 cmd 会话使用 UTF-8，保证中文输出可由后端稳定读取。"""

    return f"chcp 65001 >nul 2>&1 & {command}"


def _decode_windows_shell_output(value: bytes) -> str:
    """读取 cmd UTF-8 输出，并兼容少数仍强制使用 GBK 的旧程序。"""

    try:
        text = value.decode("utf-8")
    except UnicodeDecodeError:
        text = value.decode("gbk", errors="replace")
    return text.replace("\r\n", "\n")


def _bash_error_chunk(command: str, error: str, *, stdout: str = "") -> ToolChunk:
    """构造统一错误结果，确保前端不会只看到空白 ``Error:``。"""

    text = f"Command failed: {command}\n"
    if stdout:
        text += f"\n\nStdout:\n{stdout}"
    text += f"\n\nError:\n{error}"
    return ToolChunk(
        content=[TextBlock(text=text[:30000])],
        state=ToolResultState.ERROR,
        is_last=True,
    )

