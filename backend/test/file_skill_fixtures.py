"""把旧测试登记数据显式改成文件夹具；绝不在生产读取中模拟数据库回退。"""
from pathlib import Path
import shutil

import yaml

from src.skills import build_skill_catalog, current_skill_context
from src.skills.policy import SkillOverride, update_policy


def install_file_skills(records):
    """保留旧 ID 作为显式兼容别名，实际执行身份由文件路径生成。"""
    result = {}
    for old_id, data in records.items():
        workspace = data.get("workspaceId", "default")
        context = current_skill_context()
        target = context.tenant_root / "skills" / old_id
        target.mkdir(parents=True, exist_ok=True)
        source = Path(data.get("resourcePath") or "")
        if data.get("resourcePath") and source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)
        raw = str(data.get("content") or "")
        if not raw.startswith("---"):
            metadata = {"name": data.get("name") or old_id, "description": data.get("description") or "测试技能",
                        "category": data.get("category") or "test", "trigger": data.get("trigger", ""), "tags": data.get("tags", [])}
            raw = f"---\n{yaml.safe_dump(metadata, allow_unicode=True)}---\n\n{raw or '执行测试任务。'}\n"
        (target / "SKILL.md").write_text(raw, encoding="utf-8")
        catalog = build_skill_catalog(context)
        candidate = next(item for item in catalog.candidates if item.package_path == target.resolve())
        def configure(policy):
            policy.legacy_aliases[old_id] = [candidate.id]
            policy.overrides[candidate.id] = SkillOverride(enabled=data.get("enabled", True), shared=data.get("availableToAllAgents", True), installed=data.get("status", "installed") == "installed")
        update_policy(context.policy_path, configure)
        result[old_id] = candidate.id
    return result
