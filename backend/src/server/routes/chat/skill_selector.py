"""ReAct Runtime 的 Skill 选择器。

Router 只负责选择架构模式；具体本轮注入哪些 Skill 属于 ReAct Runtime
上下文组装阶段的职责。
"""

from src.server.routes.skills import Skill, get_installed_skill, select_relevant_skills


def select_runtime_skills(
    *,
    skill_ids: list[str],
    message: str,
    active_skill: Skill | None,
) -> list[Skill]:
    """选择本轮 ReAct Runtime 需要注入的 Skill。"""

    if active_skill:
        return [active_skill]
    available_skill_ids = {
        skill_id
        for skill_id in skill_ids
        if get_installed_skill(skill_id) is not None
    }
    return [
        skill
        for skill in select_relevant_skills(skill_ids, message)
        if skill.id in available_skill_ids
    ]
