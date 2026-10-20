"""阶段 B 审计：找出没有 await 的 async 路由，并判断调用链是否碰到事件循环 API。

用法（在 backend/ 目录）：uv run python ../openspec/changes/fix-runtime-correctness-and-quality-gates/tools/loop_affinity_audit.py
输出：可以改为 def 的路由数量，以及应写入 KEEP_ASYNC 的条目（已带注释，可直接粘贴）。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

SRC = Path("src")
ROUTE_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "api_route", "websocket"}
LOOP_APIS = {
    "create_task", "ensure_future", "get_running_loop", "get_event_loop", "call_soon",
    "call_soon_threadsafe", "run_coroutine_threadsafe", "start_soon", "put_nowait",
    "set_result", "set_exception", "call_later", "cancel",
}
MAX_DEPTH = 7


def module_name(path: Path) -> str:
    return ".".join(path.with_suffix("").parts).removesuffix(".__init__")


def is_route_decorator(node: ast.expr) -> bool:
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
        return False
    owner = node.func.value
    return node.func.attr in ROUTE_METHODS and isinstance(owner, ast.Name) and (owner.id.endswith("router") or owner.id == "app")


def awaits(fn: ast.AST) -> bool:
    stack = list(getattr(fn, "body", []))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        if isinstance(node, (ast.Await, ast.AsyncFor, ast.AsyncWith)):
            return True
        if isinstance(node, ast.comprehension) and node.is_async:
            return True
        stack.extend(ast.iter_child_nodes(node))
    return False


funcs: dict[tuple[str, str], ast.AST] = {}
imports: dict[str, dict[str, tuple[str, str | None]]] = {}
routes: list[tuple[str, tuple[str, str]]] = []
for path in sorted(SRC.rglob("*.py")):
    mod = module_name(path)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    table: dict[str, tuple[str, str | None]] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs[(mod, node.name)] = node
            if isinstance(node, ast.AsyncFunctionDef) and any(map(is_route_decorator, node.decorator_list)) and not awaits(node):
                routes.append((f"{path.relative_to(SRC).as_posix()}::{node.name}", (mod, node.name)))
        if isinstance(node, ast.ClassDef):
            for member in node.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    funcs[(mod, f"{node.name}.{member.name}")] = member
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("src"):
            for alias in node.names:
                table[alias.asname or alias.name] = (node.module, alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("src"):
                    table[alias.asname or alias.name] = (alias.name, None)
    imports[mod] = table


def direct_loop_api(node: ast.AST) -> str | None:
    for item in ast.walk(node):
        if isinstance(item, ast.Attribute):
            if item.attr in LOOP_APIS:
                return item.attr
            if item.attr == "set" and "event" in ast.unparse(item.value).lower():
                return "event.set"
        if isinstance(item, ast.Name) and item.id in LOOP_APIS:
            return item.id
    return None


def callees(mod: str, node: ast.AST) -> list[tuple[str, str]]:
    found = []
    for item in ast.walk(node):
        if not isinstance(item, ast.Call):
            continue
        func = item.func
        if isinstance(func, ast.Name):
            if (mod, func.id) in funcs:
                found.append((mod, func.id))
            elif func.id in imports[mod]:
                target_mod, name = imports[mod][func.id]
                if name and (target_mod, name) in funcs:
                    found.append((target_mod, name))
        elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id in imports[mod]:
            target_mod, name = imports[mod][func.value.id]
            owner = target_mod if name is None else f"{target_mod}.{name}"
            if (owner, func.attr) in funcs:
                found.append((owner, func.attr))
    return found


memo: dict[tuple[str, str], str | None] = {}


def reason(key: tuple[str, str], stack: tuple[tuple[str, str], ...] = ()) -> str | None:
    if not stack and "text/event-stream" in ast.unparse(funcs[key]):
        return "SSE 流式响应：只构造异步生成器，推送在事件循环里进行"
    if key in memo:
        return memo[key]
    if key in stack or len(stack) > MAX_DEPTH:
        return None
    api = direct_loop_api(funcs[key])
    result = f"{key[0].removeprefix('src.')}.{key[1]} -> {api}" if api else None
    if result is None:
        for callee in callees(key[0], funcs[key]):
            result = reason(callee, stack + (key,))
            if result:
                break
    memo[key] = result
    return result


keep = [(route, reason(key)) for route, key in routes if reason(key)]
convert = [route for route, key in routes if not reason(key)]
print(f"# async 且无 await 的路由 {len(routes)} 个：可改为 def {len(convert)} 个，应保留 {len(keep)} 个", file=sys.stderr)
for route, why in keep:
    print(f'    "{route}",  # {why}')
