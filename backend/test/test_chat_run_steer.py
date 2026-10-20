"""进行中的 Chat Run 可接收用户补充。"""

import asyncio

import pytest

from src.server.routes.chat.background_runs import (
    ChatBackgroundRun,
    steer_chat_background_run,
    take_run_steers,
)
from src.server.routes.chat import background_runs as background_runs_module


def test_steer_queues_and_drains(monkeypatch) -> None:
    from src.sessions.records import create_session

    create_session(session_id="s1")
    run = ChatBackgroundRun(id="run-1", session_id="s1", status="running")
    monkeypatch.setattr(background_runs_module, "_runs", {"run-1": run})
    monkeypatch.setattr(
        "src.server.routes.chat.store._append_user_message",
        lambda **kwargs: None,
    )
    asyncio.run(steer_chat_background_run("run-1", message="补充：只要北京的数据"))
    assert take_run_steers("run-1") == ["补充：只要北京的数据"]
    assert take_run_steers("run-1") == []


def test_steer_rejects_terminal_run(monkeypatch) -> None:
    run = ChatBackgroundRun(id="run-1", session_id="s1", status="cancelled")
    monkeypatch.setattr(background_runs_module, "_runs", {"run-1": run})
    with pytest.raises(RuntimeError):
        asyncio.run(steer_chat_background_run("run-1", message="晚了"))
