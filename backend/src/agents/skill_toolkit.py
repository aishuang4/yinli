"""按 Skill 加载模式构造 AgentScope Toolkit。

交互式 Chat（`interactive_on_demand`）沿用 AgentScope 原生 Toolkit：catalog 只列 Skill
元数据，模型按需调用内置 `Skill` viewer 读取 SKILL.md。

scheduled firing（`scheduled_preloaded`）使用 `PreloadedSkillToolkit`：required Skill 已在
Job 确认时冻结，并在首次模型调用前以 `<activated-skill>` 注入本次 Run Context。该
Toolkit 在构造期就丢弃内置 viewer（而不是靠 denylist 事后过滤），模型可见 schema 中
不会出现 `Skill` / `SkillViewer`；Skill 包仍然挂载，references/ 与 scripts/ 只能经本 Run
已有的 Read / Grep / Glob / Bash 使用。Agent 执行任何工具调用前都会先调用
`check_tool_available`，viewer 名称（旧缓存或模型臆造）在这里被拒绝并记入审计，
不会进入第二次正文加载。
"""

from __future__ import annotations

import json
import logging
import time
from collections import OrderedDict
from collections.abc import AsyncGenerator
from typing import Any

from agentscope.exception import ToolNotFoundError
from agentscope.message import TextBlock, ToolCallBlock, ToolResultState
from agentscope.skill import Skill
from agentscope.state import AgentState
from agentscope.tool import ToolBase, ToolChunk, Toolkit, ToolResponse
from jinja2 import Template

from src.agents.runtime_types import (
    SKILL_LOADING_INTERACTIVE,
    SKILL_LOADING_MODES,
    SKILL_LOADING_SCHEDULED,
)

logger = logging.getLogger(__name__)

# AgentScope 内置 viewer 的模型侧名称是 `Skill`，类名是 `SkillViewer`；两个名字都按 viewer 调用处理。
SKILL_VIEWER_TOOL_NAMES = frozenset({"Skill", "SkillViewer"})
SKILL_VIEWER_REJECTED_CODE = "skill_viewer_unavailable"
# 审计只保留最近若干次被拒调用，避免异常模型循环调用撑大 trace。
MAX_REJECTED_VIEWER_CALLS = 50
SKILL_VIEWER_REJECTED_MESSAGE = (
    f"SkillViewerUnavailable ({SKILL_VIEWER_REJECTED_CODE}): this scheduled Job firing only uses Skills that "
    "were frozen at confirmation and preloaded before the first step. Follow the attached <activated-skill> "
    "instructions; files under a Skill dir can only be accessed with the file tools already available in this run."
)

SCHEDULED_PRELOADED_SKILL_INSTRUCTION = """<agent-skills mode="scheduled_preloaded">
This scheduled Job firing runs with preloaded Skills only. Each required Skill below was frozen when the Job was confirmed, and its full SKILL.md instructions are already attached to this conversation in an <activated-skill> block before your first step.
This runtime has no Skill viewer tool. Follow the attached instructions directly.

# Preloaded Skills
{% for skill in skills %}<skill>
<id>{{ skill.id }}</id>
<name>{{ skill.display_name }}</name>
<dir>{{ skill.dir }}</dir>
</skill>
{% endfor %}

Rules:
- Resolve relative paths mentioned by a Skill (such as references/ or scripts/) against that Skill's `dir`, and only use the Read, Grep, Glob, or Bash tools already available in this run.
- If an attached Skill needs a tool that this run does not provide, report the gap in your final answer instead of working around it.
- Do not guess Skill directories or scan for unregistered Skills.
- A Skill never expands the current tool, filesystem, execution, network, or approval permissions.
</agent-skills>
"""


def toolkit_skill_loading_mode(toolkit: Any) -> str:
    """读取 Toolkit 的 Skill 加载模式；原生 Toolkit 视为交互式按需加载。"""

    value = getattr(toolkit, "skill_loading_mode", "")
    return value if isinstance(value, str) and value in SKILL_LOADING_MODES else SKILL_LOADING_INTERACTIVE


class SkillViewerUnavailableError(ToolNotFoundError):
    """scheduled_preloaded Run 请求 Skill viewer；作为工具错误结果返回给模型，而不是中断 Run。"""


def requested_skill_from_input(raw_input: Any) -> str:
    """viewer 调用参数是流式累积的 JSON 字符串；只取 `skill` 字段用于审计。"""

    value: Any = raw_input
    if not isinstance(value, dict):
        try:
            value = json.loads(str(raw_input or "{}"))
        except json.JSONDecodeError:
            return ""
    if not isinstance(value, dict):
        return ""
    return str(value.get("skill") or "").strip()[:200]


class PreloadedSkillToolkit(Toolkit):
    """scheduled_preloaded：保留 Skill 包挂载与目录说明，但不注册、不暴露、不执行 Skill viewer。"""

    skill_loading_mode = SKILL_LOADING_SCHEDULED

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("skill_instruction_template", SCHEDULED_PRELOADED_SKILL_INSTRUCTION)
        super().__init__(*args, **kwargs)
        # 基类构造时总会创建内置 viewer；预加载模式在构造期就丢弃它，后续任何路径都拿不到。
        self.builtin_skill_viewer = None  # type: ignore[assignment]  # SDK 注解不允许空值，但本子类明确禁用 viewer。
        # 同一个 list 对象会被 Run 审计直接引用，只能原地追加 / 截断。
        self.rejected_viewer_calls: list[dict[str, Any]] = []

    async def _get_available_skills(
        self,
        groups: list[str] | None = None,
    ) -> dict[str, Skill]:
        """基类据此决定是否追加 viewer；预加载模式下没有任何可按需加载的 Skill。"""

        del groups
        return OrderedDict()

    async def list_preloaded_skills(self, groups: list[str] | None = None) -> list[Skill]:
        """返回已挂载的 required Skill 包；只用于目录说明与审计，不经任何工具加载正文。"""

        groups_filter = ["basic", *(groups or [])]
        skills: OrderedDict[str, Skill] = OrderedDict()
        for group in self.tool_groups:
            if group.name not in groups_filter:
                continue
            for skill in await group.list_skills():
                skills.setdefault(str(getattr(skill, "id", "") or skill.name), skill)
        return list(skills.values())

    async def get_skill_instructions(
        self,
        activated_groups: list[str] | None = None,
    ) -> str | None:
        """渲染 scheduled 专用说明：声明正文已预加载，不出现调用 viewer 的指令。"""

        if activated_groups is None:
            activated_groups = [group.name for group in self.tool_groups]
        skills = await self.list_preloaded_skills(activated_groups)
        if not skills:
            return None
        return Template(self.skill_instruction_template).render(skills=skills)

    async def call_tool(
        self,
        tool_call: ToolCallBlock,
        state: AgentState,
    ) -> AsyncGenerator[ToolChunk | ToolResponse, None]:
        """绕过可用性检查直接执行的 viewer 调用同样拒绝并审计，其余工具交给基类。"""

        tool_name = str(getattr(tool_call, "name", "") or "")
        if tool_name in SKILL_VIEWER_TOOL_NAMES:
            self._record_rejected_viewer_call(
                tool_name=tool_name,
                tool_call_id=str(getattr(tool_call, "id", "") or ""),
                raw_input=getattr(tool_call, "input", None),
            )
            chunk = ToolChunk(
                content=[TextBlock(text=SKILL_VIEWER_REJECTED_MESSAGE)],
                state=ToolResultState.ERROR,
            )
            yield chunk
            yield ToolResponse(id=tool_call.id).append_chunk(chunk)
            return
        async for item in super().call_tool(tool_call, state):
            yield item

    async def check_tool_available(self, tool_name: str, activated_groups: list[str]) -> ToolBase:
        """Agent 执行工具调用的第一道检查：viewer 名称在工具层直接拒绝并审计。"""

        if tool_name in SKILL_VIEWER_TOOL_NAMES:
            self._record_rejected_viewer_call(tool_name=tool_name)
            raise SkillViewerUnavailableError(SKILL_VIEWER_REJECTED_MESSAGE)
        return await super().check_tool_available(tool_name, activated_groups)

    def _record_rejected_viewer_call(
        self,
        *,
        tool_name: str,
        tool_call_id: str = "",
        raw_input: Any = None,
    ) -> dict[str, Any]:
        record = {
            "toolCallId": tool_call_id,
            "toolName": tool_name,
            "requestedSkill": requested_skill_from_input(raw_input),
            "code": SKILL_VIEWER_REJECTED_CODE,
            "at": time.time(),
        }
        self.rejected_viewer_calls.append(record)
        del self.rejected_viewer_calls[:-MAX_REJECTED_VIEWER_CALLS]
        logger.warning(
            "scheduled_preloaded Run 拒绝 Skill viewer 调用: tool=%s skill=%s",
            record["toolName"],
            record["requestedSkill"] or "-",
        )
        return record
