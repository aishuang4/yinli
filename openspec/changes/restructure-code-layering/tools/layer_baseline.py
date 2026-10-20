"""生成 import-linter 分层契约的 ignore_imports 基线：列出所有"从低层直接导入高层"的边。

用法（在 backend/ 目录）：uv run python ../openspec/changes/restructure-code-layering/tools/layer_baseline.py
输出：可直接粘贴进 pyproject.toml 的 ignore_imports 列表；标准错误输出每层的边数与未归层的顶层模块。
层次定义优先读取 pyproject.toml 中 name = "backend layers" 的契约；尚未配置时使用下方 DEFAULT_LAYERS（即 design.md Decision 1 的初始层次）。
"""

from __future__ import annotations

import os
import re
import sys
import tomllib
from collections import Counter
from pathlib import Path

sys.path.insert(0, os.getcwd())

import grimp  # noqa: E402

DEFAULT_LAYERS: list[list[str]] = [
    ["src.server"],
    [
        "src.group_studio", "src.scheduler", "src.runtime", "src.subagents", "src.mcp", "src.agents",
        "src.tools", "src.memory", "src.security", "src.catalog", "src.skills", "src.sessions",
        "src.notifications", "src.sandbox", "src.vector_store", "src.project_files",
        "src.project_resources", "src.runtime_context", "src.session_migration", "src.models",
        "src.generated_file_preview",
    ],
    ["src.storage"],
    ["src.tenancy"],
    ["src.config", "src.common", "src.exception_detail"],
]


def configured_layers() -> list[list[str]]:
    pyproject = Path("pyproject.toml")
    if not pyproject.exists():
        return DEFAULT_LAYERS
    config = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("tool", {}).get("importlinter", {})
    for contract in config.get("contracts", []):
        if contract.get("name") == "backend layers":
            return [[name.strip(" ()") for name in re.split(r"[:|]", layer)] for layer in contract["layers"]]
    return DEFAULT_LAYERS


LAYERS = configured_layers()


def layer_of(module: str) -> int | None:
    top = ".".join(module.split(".")[:2])
    for index, members in enumerate(LAYERS):
        if top in members:
            return index
    return None


graph = grimp.build_graph("src")
edges: set[tuple[str, str]] = set()
unlayered: set[str] = set()
for importer in graph.modules:
    importer_layer = layer_of(importer)
    if importer_layer is None:
        if importer != "src":
            unlayered.add(".".join(importer.split(".")[:2]))
        continue
    for imported in graph.find_modules_directly_imported_by(importer):
        imported_layer = layer_of(imported)
        if imported_layer is not None and imported_layer < importer_layer:
            edges.add((importer, imported))

per_layer = Counter(layer_of(a) for a, _ in edges)
print(f"# 向上导入共 {len(edges)} 条；按导入方所在层：{dict(sorted(per_layer.items()))}", file=sys.stderr)
if unlayered:
    print(f"# 未归层的顶层模块（须加入 pyproject 的 layers）：{sorted(unlayered)}", file=sys.stderr)
print("ignore_imports = [")
for importer, imported in sorted(edges):
    print(f'    "{importer} -> {imported}",')
print("]")
