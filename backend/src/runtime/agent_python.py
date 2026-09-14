"""Agent Bash 使用的隔离 Python 环境。

桌面端目录为 ``~/.agent-workstation/python-venv``。优先用该环境，
解释器损坏或不存在时回退到用户 PATH 上的 python。
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from src.config import runtime_paths

logger = logging.getLogger(__name__)

VENV_DIRNAME = "python-venv"
_FROZEN_NAMES = {"backend-server", "backend-server.exe"}


@dataclass(frozen=True)
class AgentVenv:
    root: Path
    python: Path
    bin_dir: Path


def agent_venv_dir() -> Path:
    return Path(runtime_paths.USER_DATA_ROOT) / VENV_DIRNAME


def venv_python_path(root: Path) -> Path:
    if os.name == "nt":
        return root / "Scripts" / "python.exe"
    return root / "bin" / "python"


def venv_bin_dir(root: Path) -> Path:
    if os.name == "nt":
        return root / "Scripts"
    return root / "bin"


def _in_pytest() -> bool:
    return bool(os.getenv("PYTEST_CURRENT_TEST")) and os.getenv("AGENT_WORKSTATION_ENSURE_AGENT_VENV") != "1"


def _venv_usable(python: Path) -> bool:
    if not python.is_file():
        return False
    try:
        result = subprocess.run(
            [str(python), "-c", "import sys; print(sys.executable)"],
            check=False,
            capture_output=True,
            timeout=15,
            text=True,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and bool((result.stdout or "").strip())


def _bootstrap_python() -> str | None:
    if not getattr(sys, "frozen", False):
        name = Path(sys.executable).name.lower()
        if name not in _FROZEN_NAMES and Path(sys.executable).is_file():
            return sys.executable
    for candidate in ("python3", "python"):
        found = shutil.which(candidate)
        if found:
            return found
    return None


def resolve_agent_venv() -> AgentVenv | None:
    """已存在且可用的隔离环境；不可用则返回 None。"""

    root = agent_venv_dir()
    python = venv_python_path(root)
    if not _venv_usable(python):
        return None
    return AgentVenv(root=root, python=python, bin_dir=venv_bin_dir(root))


def ensure_agent_venv() -> AgentVenv | None:
    """创建或修复 ``python-venv``。pytest 默认跳过。"""

    existing = resolve_agent_venv()
    if existing is not None:
        return existing
    if _in_pytest():
        return None
    bootstrap = _bootstrap_python()
    if bootstrap is None:
        logger.warning("无法创建 Agent Python 环境：找不到可用的 python")
        return None
    root = agent_venv_dir()
    python = venv_python_path(root)
    if root.exists() and not _venv_usable(python):
        shutil.rmtree(root, ignore_errors=True)
    root.parent.mkdir(parents=True, exist_ok=True)
    uv = shutil.which("uv")
    try:
        if uv:
            subprocess.run(
                [uv, "venv", str(root)],
                check=True,
                capture_output=True,
                timeout=120,
                text=True,
            )
        else:
            subprocess.run(
                [bootstrap, "-m", "venv", str(root)],
                check=True,
                capture_output=True,
                timeout=120,
                text=True,
            )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        logger.warning("创建 Agent Python 环境失败: %s", exc)
        return None
    created = resolve_agent_venv()
    if created is None:
        logger.warning("Agent Python 环境创建后仍不可用: %s", root)
    return created


def agent_shell_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Bash 子进程环境：优先隔离 venv，否则沿用用户 PATH。"""

    env = dict(os.environ if base is None else base)
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)
    venv = resolve_agent_venv() or ensure_agent_venv()
    if venv is None:
        return env
    env["VIRTUAL_ENV"] = str(venv.root)
    env["PYTHONNOUSERSITE"] = "1"
    env["PATH"] = str(venv.bin_dir) + os.pathsep + env.get("PATH", "")
    return env
