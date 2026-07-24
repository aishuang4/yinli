from src.server.routes.chat.plan_mode.policy import (
    PLAN_CONTROL_TOOL_NAMES,
    is_plan_mode_tool_allowed,
)


def test_plan_control_tools_are_allowed() -> None:
    assert "plan_enter" in PLAN_CONTROL_TOOL_NAMES
    assert is_plan_mode_tool_allowed("plan_enter", "{}")
    assert is_plan_mode_tool_allowed("plan_write", "{}")
    assert is_plan_mode_tool_allowed("plan_set_steps", "{}")
    assert is_plan_mode_tool_allowed("plan_request_review", "{}")


def test_builtin_read_tools_are_allowed() -> None:
    assert is_plan_mode_tool_allowed("Read", '{"path": "README.md"}')
    assert is_plan_mode_tool_allowed("Grep", '{"pattern": "Plan"}')
    assert is_plan_mode_tool_allowed("Glob", '{"pattern": "*.py"}')
    assert is_plan_mode_tool_allowed("WebFetch", '{"url": "https://example.com"}')


def test_write_tools_are_denied() -> None:
    assert not is_plan_mode_tool_allowed("Write", '{"path": "x", "content": "y"}')
    assert not is_plan_mode_tool_allowed("Edit", '{"path": "x", "old_str": "a", "new_str": "b"}')


def test_bash_read_only_commands_are_allowed() -> None:
    assert is_plan_mode_tool_allowed("Bash", '{"command": "pwd"}')
    assert is_plan_mode_tool_allowed("Bash", '{"command": "rg -n plan backend/src"}')
    assert is_plan_mode_tool_allowed("Bash", '{"command": "git status --short"}')
    assert is_plan_mode_tool_allowed("Bash", '{"command": "git diff -- backend/src/server/routes/chat"}')


def test_bash_mutating_commands_are_denied() -> None:
    assert not is_plan_mode_tool_allowed("Bash", '{"command": "rm -rf build"}')
    assert not is_plan_mode_tool_allowed("Bash", '{"command": "git add backend/src"}')
    assert not is_plan_mode_tool_allowed("Bash", '{"command": "npm install"}')
    assert not is_plan_mode_tool_allowed("Bash", '{"command": "python script.py > out.txt"}')


def test_http_get_and_head_are_allowed_but_mutations_denied() -> None:
    assert is_plan_mode_tool_allowed("HttpRequest", '{"url": "https://example.com", "method": "GET"}')
    assert is_plan_mode_tool_allowed("HttpRequest", '{"url": "https://example.com", "method": "HEAD"}')
    assert not is_plan_mode_tool_allowed("HttpRequest", '{"url": "https://example.com", "method": "POST"}')
