"""RunState 产物文件归一化与旧工具输入推断。"""

from __future__ import annotations

import json
from pathlib import PurePosixPath
from typing import Any


def collect_generated_files_from_tools(tools: list[dict[str, Any]]) -> list[dict[str, str]]:
    """从已完成的 Write/Edit 工具摘要推断旧 Run 的产物文件。"""

    files: list[dict[str, str]] = []
    seen_paths: set[str] = set()
    for tool in tools:
        if str(tool.get("status") or "") != "completed":
            continue
        tool_name = _canonical_tool_name(str(tool.get("name") or ""))
        if tool_name not in {"write", "edit"}:
            continue
        file_path = _extract_file_path_from_tool_input(str(tool.get("inputPreview") or ""))
        if not file_path or file_path in seen_paths:
            continue
        seen_paths.add(file_path)
        files.append(_generated_file_summary(file_path))
    return files


def generated_files_from_state(
    state: dict[str, Any],
    tools: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """优先使用真实扫描的 Session 产物，兼容旧 Write/Edit 工具推断。"""

    artifacts = state.get("generatedFiles")
    if isinstance(artifacts, list):
        files = normalize_generated_files(artifacts)
        if files:
            return files
    return collect_generated_files_from_tools(tools)


def normalize_generated_files(items: list[Any]) -> list[dict[str, str]]:
    """归一化 Session 产物清单，消除重复和不完整记录。"""

    files: list[dict[str, str]] = []
    seen_paths: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        file_path = str(item.get("path") or "").strip()
        if not file_path or file_path in seen_paths:
            continue
        seen_paths.add(file_path)
        path = PurePosixPath(file_path)
        files.append({
            "path": file_path,
            "name": str(item.get("name") or path.name or "file"),
            "extension": str(item.get("extension") or path.suffix.lstrip(".").upper() or "FILE"),
        })
    return files


def _canonical_tool_name(name: str) -> str:
    value = name.strip().lower()
    parts = [part for part in value.split("_") if part]
    if len(parts) == 2 and parts[0] == parts[1]:
        return parts[0]
    return value


def _extract_file_path_from_tool_input(input_preview: str) -> str:
    if not input_preview:
        return ""
    decoded = _try_decode_json_object(input_preview)
    if decoded:
        for key in ("file_path", "filePath", "path", "target_file", "targetFile"):
            value = decoded.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return _extract_file_path_from_text(input_preview)


def _try_decode_json_object(value: str) -> dict[str, Any]:
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _extract_file_path_from_text(value: str) -> str:
    markers = ('"file_path"', "'file_path'", '"filePath"', "'filePath'", '"path"', "'path'")
    for marker in markers:
        index = value.find(marker)
        if index < 0:
            continue
        tail = value[index + len(marker):]
        colon = tail.find(":")
        if colon < 0:
            continue
        raw = tail[colon + 1:].lstrip()
        if not raw:
            continue
        quote = raw[0] if raw[0] in {'"', "'"} else ""
        if not quote:
            continue
        end = raw.find(quote, 1)
        if end > 1:
            return raw[1:end].strip()
    return ""


def _generated_file_summary(file_path: str) -> dict[str, str]:
    path = PurePosixPath(file_path)
    name = path.name or file_path
    return {
        "path": file_path,
        "name": name,
        "extension": PurePosixPath(name).suffix.lstrip(".").upper() or "FILE",
    }
