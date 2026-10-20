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
    external_skills: list[Skill] | None = None,
) -> list[Skill]:
    """返回本轮可发现的 Skills；具体使用哪个 Skill 由模型按需决定。"""

    del message
    from src.skills.registry import resolve_installed_skills
    available, _missing = resolve_installed_skills(skill_ids, external_skills=external_skills)
    # 激活状态不能额外扩大本轮授权目录。
    return available
