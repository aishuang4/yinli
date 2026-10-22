"""冻结产品域对 Chat 内部模块的 import。

群聊和定时任务只能走 src.runtime 公开入口。其他域已经存在的 Chat 依赖写在
ALLOWLIST 里；新增或漏删都会失败。src/runtime 是适配层，允许调用 Chat 内部模块。
"""
from __future__ import annotations


import ast
from pathlib import Path

BACKEND_SRC = Path(__file__).resolve().parents[1] / "src"
CHAT_PREFIX = "src.server.routes.chat"
SEALED_ROOTS = ("group_studio/", "scheduler/")
PUBLIC_ENTRY = (
    "src.runtime.agent_spec、src.runtime.governance、src.runtime.chat_session、"
    "src.runtime.agent_resources、src.runtime.scheduled_chat、src.runtime.capabilities、"
    "src.runtime.model_call_trace、src.runtime.user_note"
)

# 文件相对 backend/src，模块，导入名。群聊和定时任务不在这里。
ALLOWLIST = {
    ("memory/trace.py", "src.server.routes.chat.trace_recorder", "_record_run_event"),
    ("server/main.py", "src.server.routes.chat.team_runtime.maintenance", "maintain_team_runtime_runs"),
    ("subagents/manager.py", "src.server.routes.chat.hitl", "_create_pending_interaction"),
    ("subagents/manager.py", "src.server.routes.chat.runtime_events", "build_runtime_event"),
    ("subagents/manager.py", "src.server.routes.chat.trace_recorder", "_record_run_event"),
    ("subagents/runner.py", "src.server.routes.chat.trace_recorder", "_record_agent_scope_event"),
    ("subagents/runner.py", "src.server.routes.chat.trace_recorder", "_record_permission_decision"),
    ("subagents/runtime_context.py", "src.server.routes.chat.runtime_governance", "build_actor_memory_prompt"),
    ("tools/memory_nodes.py", "src.server.routes.chat.background_runs", "current_chat_background_run_id"),
}


def _chat_imports() -> set[tuple[str, str, str]]:
    found: set[tuple[str, str, str]] = set()
    for path in BACKEND_SRC.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        relative = path.relative_to(BACKEND_SRC).as_posix()
        if relative.startswith("server/routes/chat/") or relative.startswith("runtime/"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            modules: list[str] = []
            names: list[str] = []
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith(CHAT_PREFIX):
                modules.append(node.module)
                names.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(CHAT_PREFIX):
                        modules.append(alias.name)
                        names.append(alias.asname or alias.name.rsplit(".", 1)[-1])
            for module in modules:
                for name in names:
                    found.add((relative, module, name))
    return found


def test_new_chat_imports_stay_on_the_allowlist() -> None:
    actual = _chat_imports()
    sealed = {item for item in actual if item[0].startswith(SEALED_ROOTS)}
    assert sealed == set(), (
        "群聊和定时任务不能 import Chat 内部模块：\n"
        + "\n".join(f"{path} -> {module}.{name}" for path, module, name in sorted(sealed))
        + f"\n请改用公开入口 {PUBLIC_ENTRY}。"
    )
    rest = actual - sealed
    extra = rest - ALLOWLIST
    stale = ALLOWLIST - rest
    assert extra == set(), (
        "出现了 allowlist 之外的 Chat 依赖：\n"
        + "\n".join(f"{path} -> {module}.{name}" for path, module, name in sorted(extra))
        + f"\n请改用公开入口 {PUBLIC_ENTRY}，不要把新的 routes.chat 依赖加进 allowlist。"
    )
    assert stale == set(), (
        "allowlist 里这些依赖已经不存在，请删掉：\n"
        + "\n".join(f"{path} -> {module}.{name}" for path, module, name in sorted(stale))
    )


PUBLIC_RUNTIME_MODULES = {
    "src.runtime",
    "src.runtime.agent_python",
    "src.runtime.agent_resources",
    "src.runtime.agent_spec",
    "src.runtime.chat_session",
    "src.runtime.governance",
    "src.runtime.model_call_trace",
    "src.runtime.scheduled_chat",
    "src.runtime.capabilities",
    "src.runtime.user_note",
}


def test_group_studio_and_scheduler_use_public_runtime_entries() -> None:
    """这两个域接触 Chat 时，只能 import src.runtime 里的公开模块。"""

    offenders: list[str] = []
    for path in BACKEND_SRC.rglob("*.py"):
        relative = path.relative_to(BACKEND_SRC).as_posix()
        if not relative.startswith(SEALED_ROOTS) or "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
            elif isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            for module in modules:
                if module == "src.runtime" or module.startswith("src.runtime."):
                    if module not in PUBLIC_RUNTIME_MODULES:
                        offenders.append(f"{relative} import 了未公开的 {module}")
                elif module.startswith(CHAT_PREFIX):
                    offenders.append(f"{relative} 直接 import {module}，请改用 {PUBLIC_ENTRY}")
    assert offenders == []
