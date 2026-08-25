"""模型凭据安全存储。

macOS 使用 Keychain，Windows 使用绑定当前用户的 DPAPI；SQLite 只保存凭据是否
已配置，不保存 API Key 明文。测试可通过 ``AGENT_WORKSTATION_SECRET_STORE=memory``
启用进程内存适配器。
"""

from __future__ import annotations

import ctypes
import errno
import hashlib
import os
import select
import subprocess
import sys
import time
from pathlib import Path
from threading import RLock

from src.config.runtime_paths import DATA_ROOT


SERVICE_NAME = "agent-workstation-desktop.model-credentials.v1"
SECRET_DIR = DATA_ROOT / "secrets"
_cache: dict[str, str] = {}
_memory_secrets: dict[str, str] = {}
_lock = RLock()


class SecretStoreError(RuntimeError):
    """系统安全凭据存储不可用。"""


def _backend_name() -> str:
    configured = os.getenv("AGENT_WORKSTATION_SECRET_STORE", "").strip().lower()
    if configured:
        return configured

    # 只有 Electron 桌面后端具备稳定的应用身份和系统凭据库授权。
    # Web/源码模式不能默认访问 macOS Keychain，否则浏览器版启动时会因
    # 当前进程没有 Keychain 授权而无法读取系统设置。显式配置仍优先。
    desktop_mode = os.getenv("AGENT_WORKSTATION_DESKTOP_MODE", "").strip() == "1"
    if sys.platform == "darwin" and desktop_mode:
        return "keychain"
    if sys.platform == "win32":
        return "dpapi" if desktop_mode else "memory"
    if sys.platform == "darwin":
        return "memory"
    return "unsupported"


def _account(provider_id: str) -> str:
    return f"model-provider:{provider_id}"


def get_model_api_key(provider_id: str) -> str:
    """读取供应商 API Key。"""

    account = _account(provider_id)
    with _lock:
        if account in _cache:
            return _cache[account]
        secret = _read_secret(account)
        _cache[account] = secret
        return secret


def set_model_api_key(provider_id: str, value: str) -> None:
    """安全保存供应商 API Key。"""

    secret = value.strip()
    if not secret:
        delete_model_api_key(provider_id)
        return
    if "\n" in secret or "\r" in secret:
        raise SecretStoreError("API Key 不能包含换行符。")
    account = _account(provider_id)
    with _lock:
        _write_secret(account, secret)
        _cache[account] = secret


def delete_model_api_key(provider_id: str) -> None:
    """删除供应商 API Key。"""

    account = _account(provider_id)
    with _lock:
        _delete_secret(account)
        _cache[account] = ""


def _read_secret(account: str) -> str:
    backend = _backend_name()
    if backend == "memory":
        return _memory_secrets.get(account, "")
    if backend == "keychain":
        return _keychain_read(account)
    if backend == "dpapi":
        return _dpapi_read(account)
    raise SecretStoreError("当前系统不支持安全凭据存储，请使用 macOS 或 Windows 桌面版。")


def _write_secret(account: str, value: str) -> None:
    backend = _backend_name()
    if backend == "memory":
        _memory_secrets[account] = value
        return
    if backend == "keychain":
        _keychain_write(account, value)
        return
    if backend == "dpapi":
        _dpapi_write(account, value)
        return
    raise SecretStoreError("当前系统不支持安全凭据存储，请使用 macOS 或 Windows 桌面版。")


def _delete_secret(account: str) -> None:
    backend = _backend_name()
    if backend == "memory":
        _memory_secrets.pop(account, None)
        return
    if backend == "keychain":
        _keychain_delete(account)
        return
    if backend == "dpapi":
        _dpapi_delete(account)
        return
    raise SecretStoreError("当前系统不支持安全凭据存储，请使用 macOS 或 Windows 桌面版。")


def _run_security(args: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["/usr/bin/security", *args],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SecretStoreError("macOS Keychain 暂时不可用。") from exc


def _keychain_read(account: str) -> str:
    result = _run_security([
        "find-generic-password",
        "-a", account,
        "-s", SERVICE_NAME,
        "-w",
    ])
    if result.returncode == 0:
        return result.stdout.rstrip("\r\n")
    if result.returncode == 44 or "could not be found" in result.stderr.lower():
        return ""
    raise SecretStoreError("无法从 macOS Keychain 读取 API Key。")


def _keychain_write(account: str, value: str) -> None:
    result_code = _run_security_password_prompt([
        "add-generic-password",
        "-U",
        "-a", account,
        "-s", SERVICE_NAME,
        "-w",
    ], value)
    if result_code != 0:
        raise SecretStoreError("无法将 API Key 写入 macOS Keychain。")


def _run_security_password_prompt(args: list[str], value: str) -> int:
    """通过伪终端向 security 密码提示输入 Key，避免出现在进程参数中。"""

    if sys.platform != "darwin":
        raise SecretStoreError("Keychain 只能在 macOS 上使用。")
    try:
        import pty

        pid, terminal_fd = pty.fork()
    except OSError as exc:
        raise SecretStoreError("macOS Keychain 暂时不可用。") from exc
    if pid == 0:
        try:
            os.execv("/usr/bin/security", ["/usr/bin/security", *args])
        except OSError:
            os._exit(127)

    deadline = time.monotonic() + 10
    reaped = False
    prompt_buffer = b""
    submission_count = 0
    try:
        while time.monotonic() < deadline:
            waited_pid, wait_status = os.waitpid(pid, os.WNOHANG)
            if waited_pid == pid:
                reaped = True
                return os.waitstatus_to_exitcode(wait_status)
            readable, _, _ = select.select([terminal_fd], [], [], 0.1)
            if readable:
                try:
                    output = os.read(terminal_fd, 4096)
                    prompt_buffer = (prompt_buffer + output)[-1024:]
                    normalized_prompt = prompt_buffer.lower()
                    needs_password = (
                        submission_count == 0
                        and b"password data" in normalized_prompt
                    )
                    needs_confirmation = (
                        submission_count == 1
                        and b"retype password" in normalized_prompt
                    )
                    if needs_password or needs_confirmation:
                        os.write(terminal_fd, f"{value}\n".encode("utf-8"))
                        submission_count += 1
                        prompt_buffer = b""
                except OSError as exc:
                    if exc.errno != errno.EIO:
                        raise
        raise SecretStoreError("macOS Keychain 写入超时。")
    except OSError as exc:
        raise SecretStoreError("macOS Keychain 暂时不可用。") from exc
    finally:
        os.close(terminal_fd)
        if not reaped:
            try:
                os.kill(pid, 9)
            except ProcessLookupError:
                pass
            os.waitpid(pid, 0)


def _keychain_delete(account: str) -> None:
    result = _run_security([
        "delete-generic-password",
        "-a", account,
        "-s", SERVICE_NAME,
    ])
    if result.returncode not in (0, 44) and "could not be found" not in result.stderr.lower():
        raise SecretStoreError("无法从 macOS Keychain 删除 API Key。")


class _DataBlob(ctypes.Structure):
    """Windows DATA_BLOB。"""

    _fields_ = [
        ("cbData", ctypes.c_ulong),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def _to_blob(value: bytes) -> tuple[_DataBlob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(value)
    blob = _DataBlob(
        len(value),
        ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)),
    )
    return blob, buffer


def _dpapi_path(account: str) -> Path:
    digest = hashlib.sha256(account.encode("utf-8")).hexdigest()
    return SECRET_DIR / f"{digest}.dpapi"


def _windows_crypto_apis():
    """绑定 64 位安全的 Windows DPAPI / LocalFree 函数签名。"""

    from ctypes import wintypes

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    blob_pointer = ctypes.POINTER(_DataBlob)
    crypt32.CryptProtectData.argtypes = [
        blob_pointer,
        ctypes.c_wchar_p,
        blob_pointer,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        blob_pointer,
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [
        blob_pointer,
        ctypes.POINTER(ctypes.c_wchar_p),
        blob_pointer,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        blob_pointer,
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    return crypt32, kernel32


def _dpapi_protect(value: bytes) -> bytes:
    if sys.platform != "win32":
        raise SecretStoreError("DPAPI 只能在 Windows 上使用。")
    input_blob, input_buffer = _to_blob(value)
    output_blob = _DataBlob()
    crypt32, kernel32 = _windows_crypto_apis()
    success = crypt32.CryptProtectData(
        ctypes.byref(input_blob),
        "Agent Workstation model API key",
        None,
        None,
        None,
        0x01,
        ctypes.byref(output_blob),
    )
    del input_buffer
    if not success:
        raise SecretStoreError("Windows DPAPI 加密 API Key 失败。")
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(output_blob.pbData, ctypes.c_void_p))


def _dpapi_unprotect(value: bytes) -> bytes:
    if sys.platform != "win32":
        raise SecretStoreError("DPAPI 只能在 Windows 上使用。")
    input_blob, input_buffer = _to_blob(value)
    output_blob = _DataBlob()
    crypt32, kernel32 = _windows_crypto_apis()
    success = crypt32.CryptUnprotectData(
        ctypes.byref(input_blob),
        None,
        None,
        None,
        None,
        0x01,
        ctypes.byref(output_blob),
    )
    del input_buffer
    if not success:
        raise SecretStoreError("Windows DPAPI 解密 API Key 失败。")
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(output_blob.pbData, ctypes.c_void_p))


def _dpapi_read(account: str) -> str:
    path = _dpapi_path(account)
    if not path.exists():
        return ""
    try:
        return _dpapi_unprotect(path.read_bytes()).decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise SecretStoreError("无法读取 Windows 安全凭据文件。") from exc


def _dpapi_delete(account: str) -> None:
    try:
        _dpapi_path(account).unlink(missing_ok=True)
    except OSError as exc:
        raise SecretStoreError("无法删除 Windows 安全凭据文件。") from exc


def _dpapi_write(account: str, value: str) -> None:
    path = _dpapi_path(account)
    temporary_path = path.with_suffix(".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path.write_bytes(_dpapi_protect(value.encode("utf-8")))
        temporary_path.chmod(0o600)
        temporary_path.replace(path)
    except OSError as exc:
        raise SecretStoreError("无法写入 Windows 安全凭据文件。") from exc
