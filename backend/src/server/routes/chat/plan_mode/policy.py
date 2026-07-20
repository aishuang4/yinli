"""Plan Mode 工具准入策略。

Plan Mode 只允许只读调查工具和计划控制工具执行；变更型工具由 middleware
合成 DENIED 工具结果，避免真实修改外部状态。
"""

import json
import shlex
from typing import Any


PLAN_CONTROL_TOOL_NAMES = frozenset({
    "plan_enter",
    "plan_write",
    "plan_exit",
    "todo_write",
    "task_list",
    "task_output",
    "FinalAnswerStart",
})

READ_ONLY_TOOL_NAMES = frozenset({
    "Read",
    "Grep",
    "Glob",
    "KnowledgeBaseSearch",
    "WebFetch",
})

BASH_READ_ONLY_COMMANDS = frozenset({
    "cat",
    "find",
    "git",
    "grep",
    "head",
    "ls",
    "pwd",
    "rg",
    "sed",
    "tail",
    "wc",
})

GIT_READ_ONLY_SUBCOMMANDS = frozenset({
    "branch",
    "diff",
    "log",
    "show",
    "status",
})

BASH_MUTATION_TOKENS = frozenset({
    ">",
    ">>",
    "<",
    "|",
    "&&",
    "||",
    ";",
    "rm",
    "mv",
    "cp",
    "touch",
    "mkdir",
    "chmod",
    "chown",
    "git add",
    "git commit",
    "git push",
    "git merge",
    "git switch",
    "git checkout",
    "npm install",
    "pnpm install",
    "yarn install",
    "pip install",
    "uv add",
})


def is_plan_mode_tool_allowed(tool_name: str | None, raw_input: str | dict[str, Any] | None) -> bool:
    """判断 Plan Mode 下某个工具调用是否允许真实执行。"""

    if not tool_name:
        return False
    if tool_name in PLAN_CONTROL_TOOL_NAMES or tool_name in READ_ONLY_TOOL_NAMES:
        return True
    parsed_input = _parse_input(raw_input)
    if tool_name == "Bash":
        return _is_read_only_bash(str(parsed_input.get("command") or ""))
    if tool_name == "HttpRequest":
        return str(parsed_input.get("method") or "GET").upper() in {"GET", "HEAD"}
    return False


def _parse_input(raw_input: str | dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(raw_input, dict):
        return raw_input
    if not raw_input:
        return {}
    try:
        value = json.loads(raw_input)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}


def _is_read_only_bash(command: str) -> bool:
    compact = " ".join(command.strip().split())
    if not compact:
        return False
    lowered = compact.lower()
    if any(token in lowered for token in BASH_MUTATION_TOKENS):
        return False
    try:
        parts = shlex.split(compact)
    except ValueError:
        return False
    if not parts:
        return False
    executable = parts[0]
    if executable not in BASH_READ_ONLY_COMMANDS:
        return False
    if executable == "git":
        return len(parts) >= 2 and parts[1] in GIT_READ_ONLY_SUBCOMMANDS
    return True
