"""ReAct Runtime 的 Skill 选择器。

Router 只负责选择架构模式；具体本轮注入哪些 Skill 属于 ReAct Runtime
上下文组装阶段的职责。
"""

from src.server.routes.skills import Skill, get_installed_skill


def select_runtime_skills(
    *,
    skill_ids: list[str],
    message: str,
    active_skill: Skill | None,
) -> list[Skill]:
    """返回本轮可发现的 Skills；具体使用哪个 Skill 由模型按需决定。"""

    del message
    available = [
        skill
        for skill_id in skill_ids
        if (skill := get_installed_skill(skill_id)) is not None
    ]
    if active_skill and all(skill.id != active_skill.id for skill in available):
        available.insert(0, active_skill)
    return available
