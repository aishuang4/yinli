"""全局 catalog 衝突映射。相同正文才合併，不同正文不覆蓋。"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def map_same_id_files(paths: list[Path]) -> dict[str, Any]:
    """按檔名 ID 分組。內容相同合併並保留來源；內容不同只報告衝突。"""

    groups: dict[str, list[Path]] = {}
    for path in paths:
        groups.setdefault(path.stem, []).append(path)
    merged: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    for resource_id, items in sorted(groups.items()):
        digests = {_digest(item): item for item in items}
        sources = [str(item) for item in items]
        if len(digests) == 1:
            merged.append({
                "id": resource_id,
                "sha256": next(iter(digests)),
                "sources": sources,
                "action": "merge",
            })
        else:
            conflicts.append({
                "id": resource_id,
                "sources": sources,
                "action": "block",
            })
    return {"merged": merged, "conflicts": conflicts, "overwritten": []}


def apply_mapping_to_grants(
    grants: list[dict[str, str]],
    mapping: dict[str, Any],
) -> list[dict[str, str]]:
    """合併項保持同一 ID。衝突項不改 grant，並標成未解析。"""

    blocked = {item["id"] for item in mapping["conflicts"]}
    merged = {item["id"] for item in mapping["merged"]}
    result: list[dict[str, str]] = []
    for grant in grants:
        resource_id = grant["resourceId"]
        if resource_id in blocked:
            result.append({**grant, "resolution": "unresolved"})
        elif resource_id in merged:
            result.append({**grant, "resourceId": resource_id, "resolution": "mapped"})
        else:
            result.append({**grant, "resolution": "unchanged"})
    return result


def materialize_merged_files(mapping: dict[str, Any], destination: Path) -> list[str]:
    """只在目標不存在或內容相同時放入全局目錄，絕不覆蓋不同正文。"""

    written: list[str] = []
    destination.mkdir(parents=True, exist_ok=True)
    for item in mapping["merged"]:
        source = Path(item["sources"][0])
        target = destination / f"{item['id']}{source.suffix}"
        if target.exists() and _digest(target) != item["sha256"]:
            mapping["conflicts"].append({
                "id": item["id"],
                "sources": [str(target), *item["sources"]],
                "action": "block",
            })
            continue
        if not target.exists():
            target.write_bytes(source.read_bytes())
            written.append(str(target))
    return written
