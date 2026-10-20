"""阶段 B 审计：在开启 F401 之前，找出被其他模块或测试使用的"未使用导入"（隐式再导出）。

用法（在 backend/ 目录）：uv run python ../openspec/changes/fix-runtime-correctness-and-quality-gates/tools/reexport_audit.py
输出：每个再导出名字所在文件、行号，以及使用它的文件（最多 3 个）。运行约 1 分钟。
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

result = subprocess.run(
    ["uv", "run", "--no-sync", "ruff", "check", "src", "--select", "F401", "--output-format", "json", "--exit-zero", "-q"],
    capture_output=True, text=True, check=True,
)
diagnostics = json.loads(result.stdout or "[]")
texts = {p.as_posix(): p.read_text(encoding="utf-8") for p in [*Path("src").rglob("*.py"), *Path("test").rglob("*.py")]}
root = Path.cwd().resolve()
total = 0
for item in diagnostics:
    rel = Path(item["filename"]).resolve().relative_to(root).as_posix()
    module = rel[:-3].replace("/", ".").removesuffix(".__init__")
    name = re.search(r"`([^`]+)`", item["message"]).group(1).split(".")[-1]
    tail = re.escape(module.split(".")[-1])
    patterns = [
        rf"from {re.escape(module)} import [^\n]*\b{re.escape(name)}\b",
        rf"from {re.escape(module)} import \([^)]*\b{re.escape(name)}\b",
        rf"{re.escape(module)}\.{re.escape(name)}\b",
        rf"\b{tail}(_routes|_module)?\.{re.escape(name)}\b",
    ]
    users = sorted(path for path, text in texts.items() if path != rel and any(re.search(p, text, re.S) for p in patterns))
    if users:
        total += 1
        print(f"{rel}:{item['location']['row']} {name} <- {', '.join(users[:3])}")
print(f"# 隐式再导出共 {total} 个，F401 总数 {len(diagnostics)}")
