"""Agent 隔离 Python 环境：优先 python-venv，失效再回退 PATH。"""

from pathlib import Path

from src.runtime import agent_python


def test_agent_shell_env_prefers_venv(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(agent_python.runtime_paths, "USER_DATA_ROOT", tmp_path)
    root = tmp_path / "python-venv"
    bin_dir = root / ("Scripts" if agent_python.os.name == "nt" else "bin")
    bin_dir.mkdir(parents=True)
    python = bin_dir / ("python.exe" if agent_python.os.name == "nt" else "python")
    python.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    python.chmod(0o755)
    monkeypatch.setattr(agent_python, "_venv_usable", lambda path: Path(path) == python)
    env = agent_python.agent_shell_env({"PATH": "/usr/bin", "PYTHONHOME": "/old"})
    assert str(bin_dir) in env["PATH"].split(agent_python.os.pathsep)
    assert env["VIRTUAL_ENV"] == str(root)
    assert "PYTHONHOME" not in env


def test_agent_shell_env_falls_back_when_venv_broken(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(agent_python.runtime_paths, "USER_DATA_ROOT", tmp_path)
    monkeypatch.setattr(agent_python, "resolve_agent_venv", lambda: None)
    monkeypatch.setattr(agent_python, "ensure_agent_venv", lambda: None)
    env = agent_python.agent_shell_env({"PATH": "/usr/bin"})
    assert env["PATH"] == "/usr/bin"
    assert "VIRTUAL_ENV" not in env


def test_ensure_agent_venv_creates_usable_env(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(agent_python.runtime_paths, "USER_DATA_ROOT", tmp_path)
    monkeypatch.setenv("AGENT_WORKSTATION_ENSURE_AGENT_VENV", "1")
    created = agent_python.ensure_agent_venv()
    assert created is not None
    assert created.python.is_file()
    env = agent_python.agent_shell_env({"PATH": "/usr/bin"})
    assert str(created.bin_dir) == env["PATH"].split(agent_python.os.pathsep)[0]
