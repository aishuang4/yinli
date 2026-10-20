"""系統 Agent、Job Agent 與系統 Skill 的全局 seed。

不建立 Workspace / Project。使用者 Agent 的更新仍走 `<tenant-root>/agents`。
"""

from __future__ import annotations

from src.agents.system_agents import is_system_agent_id
from src.catalog.loader import list_registered_agents
from src.catalog.skill_seed import seed_system_skills


def seed_global_agents() -> list[str]:
    """重複呼叫只重掃目錄，不寫 Project，也不覆蓋使用者改過的系統包。"""

    seed_system_skills()
    return sorted(agent.id for agent in list_registered_agents() if is_system_agent_id(agent.id))
