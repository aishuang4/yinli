"""脚本型定时任务：校验脚本规格，并在 firing 目录里直接执行。"""

from __future__ import annotations

import asyncio
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")
_INTERPRETERS = frozenset({"python3", "bash"})
_MAX_SOURCE_CHARS = 200_000
_MAX_ARGS = 20
_MAX_ARG_CHARS = 200
_DEFAULT_TIMEOUT = 300
_MAX_TIMEOUT = 3600
_OUTPUT_LIMIT = 8000


class ScriptJobError(ValueError):
    """脚本任务规格或执行不合法。"""


@dataclass(frozen=True)
class ScriptRunResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False


def build_script_input_spec(
    *,
    script_filename: str,
    script_source: str,
    interpreter: str = "python3",
    args: list[str] | None = None,
    timeout_seconds: int = _DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """规范化脚本规格，供 input_spec 和 Job 文件保存。"""

    filename = str(script_filename or "").strip()
    source = str(script_source or "")
    runtime = str(interpreter or "python3").strip() or "python3"
    if not _FILENAME.fullmatch(filename) or "/" in filename or "\\" in filename:
        raise ScriptJobError("script_filename 只能是当前目录下的文件名")
    if runtime not in _INTERPRETERS:
        raise ScriptJobError("interpreter 只能是 python3 或 bash")
    suffix = Path(filename).suffix.lower()
    if runtime == "python3" and suffix != ".py":
        raise ScriptJobError("python3 脚本文件名必须以 .py 结尾")
    if runtime == "bash" and suffix != ".sh":
        raise ScriptJobError("bash 脚本文件名必须以 .sh 结尾")
    if not source.strip():
        raise ScriptJobError("script_source 不能为空")
    if len(source) > _MAX_SOURCE_CHARS:
        raise ScriptJobError("script_source 过长")
    cleaned_args: list[str] = []
    for item in list(args or []):
        text = str(item)
        if len(text) > _MAX_ARG_CHARS:
            raise ScriptJobError("单个脚本参数过长")
        cleaned_args.append(text)
    if len(cleaned_args) > _MAX_ARGS:
        raise ScriptJobError("脚本参数过多")
    try:
        timeout = int(timeout_seconds)
    except (TypeError, ValueError) as exc:
        raise ScriptJobError("timeout_seconds 必须是整数") from exc
    if not 1 <= timeout <= _MAX_TIMEOUT:
        raise ScriptJobError(f"timeout_seconds 必须在 1 到 {_MAX_TIMEOUT} 之间")
    return {
        "script_filename": filename,
        "script_source": source,
        "interpreter": runtime,
        "args": cleaned_args,
        "timeout_seconds": timeout,
    }


def script_spec_of(input_spec: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(input_spec or {})
    return build_script_input_spec(
        script_filename=str(payload.get("script_filename") or ""),
        script_source=str(payload.get("script_source") or ""),
        interpreter=str(payload.get("interpreter") or "python3"),
        args=list(payload.get("args") or []),
        timeout_seconds=int(payload.get("timeout_seconds") or _DEFAULT_TIMEOUT),
    )


def _python_executable() -> str:
    from src.runtime.agent_python import resolve_agent_venv

    venv = resolve_agent_venv()
    if venv is not None:
        return str(venv.python)
    found = shutil.which("python3") or shutil.which("python")
    if not found:
        raise ScriptJobError("找不到 python3，无法运行脚本任务")
    return found


async def execute_script_in_dir(
    *,
    source: str,
    filename: str,
    interpreter: str,
    args: list[str],
    timeout_seconds: int,
    cwd: Path,
) -> ScriptRunResult:
    """把脚本写入 firing 目录后直接执行，不经过模型。"""

    cwd.mkdir(parents=True, exist_ok=True)
    script_path = (cwd / filename).resolve()
    if script_path.parent != cwd.resolve():
        raise ScriptJobError("脚本路径超出本次运行目录")
    script_path.write_text(source, encoding="utf-8")
    from src.tenancy.context import TenantMissing, current_tenant

    try:
        hosted = current_tenant().is_hosted
    except TenantMissing:
        hosted = False
    if hosted:
        from src.sandbox.hosted_workspace import build_hosted_spec
        from src.sandbox.provider import get_sandbox_provider

        spec = build_hosted_spec(
            tenant_id=current_tenant().tenant_id,
            project_slug="script",
            agent_id=filename,
            fallback_root=cwd,
            extra="script",
        )
        provider = get_sandbox_provider()
        provider.create(spec)
        command = ["python3" if interpreter == "python3" else "bash", filename, *args]
        output = provider.exec(spec.name, command)
        return ScriptRunResult(stdout=output[:8000], stderr="", exit_code=0)
    executable = _python_executable() if interpreter == "python3" else (shutil.which("bash") or "bash")
    if interpreter == "bash" and not shutil.which("bash"):
        raise ScriptJobError("找不到 bash，无法运行脚本任务")
    process = await asyncio.create_subprocess_exec(
        executable,
        str(script_path),
        *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    timed_out = False
    try:
        stdout_raw, stderr_raw = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        timed_out = True
        process.kill()
        stdout_raw, stderr_raw = await process.communicate()
    stdout = (stdout_raw or b"").decode("utf-8", errors="replace")[-_OUTPUT_LIMIT:]
    stderr = (stderr_raw or b"").decode("utf-8", errors="replace")[-_OUTPUT_LIMIT:]
    return ScriptRunResult(
        exit_code=124 if timed_out else int(process.returncode or 0),
        stdout=stdout,
        stderr=stderr,
        timed_out=timed_out,
    )
