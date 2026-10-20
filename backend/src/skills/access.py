"""文件 Skill 的 Project binding 归一；旧/新 ID 指向同一包时停用优先。"""
from src.skills.catalog import resolve_skill
from src.skills.models import SkillError


def skill_binding_ids(catalog, bindings):
    """缺数据库行不代表授权；只认当前 catalog 内可定位的绑定。"""
    allowed, denied, refs = set(), set(), {}
    for binding in bindings:
        if binding.get("resourceType") != "skill":
            continue
        reference = str(binding.get("resourceId") or "")
        try:
            item = resolve_skill(reference, catalog, require_enabled=False).candidate
        except SkillError:
            continue
        refs.setdefault(item.id, set()).update((reference, item.id, item.load_name, *item.legacy_aliases))
        (allowed if binding.get("enabled") else denied).add(item.id)
    return frozenset(ref for identity in allowed - denied for ref in refs[identity])
