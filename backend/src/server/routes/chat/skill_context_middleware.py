"""已启用 Skill 正文的会话级保留。

主流 Agent Skills 的生命周期是：未触发时上下文里只有 name + description；
模型用 `Skill` 工具读过 SKILL.md 后，正文留在会话里供后续 ReAct 步骤和
后续几轮用户消息使用；上下文压缩后再把已启用的正文塞回去；references/
与脚本仍按需读取。

AgentScope 的 `SkillViewer` 只是把 SKILL.md 作为一条 tool result 写进
`state.context`，它会被 `tool_result_limit` 截断、被 `compress_context()`
摘要掉、在实例重建（重启 / 淘汰）后彻底丢失，模型只能再读一遍。本模块
用 middleware 补上这段生命周期：

- `on_acting` 捕获 `Skill` 工具的完整输出，记入 `state.middle_context`
  （随 `carry_over_context` 一起在实例之间搬运）；
- `on_reply` / `on_reasoning` / `on_compress_context` 检查每个已启用 Skill
  的正文是否仍完整地在 context 里，缺失或被截断时以 HintBlock 重新附上；
- 总量受预算约束，超出时淘汰最早启用的 Skill；模型再次调用 `Skill`
  会重新启用。

平台侧的 `SkillRun`（每轮关闭）不受影响：它记录的是"本轮用了哪个 Skill"，
这里保留的是"模型已经读过哪些说明书"。
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncGenerator, Awaitable, Callable, Iterable
from typing import Any

from agentscope.message import AssistantMsg, HintBlock
from agentscope.middleware import MiddlewareBase

logger = logging.getLogger(__name__)

ACTIVATED_SKILLS_KEY = "aw_activated_skills"
PENDING_CHECK_KEY = "aw_activated_skills_pending_check"
SKILL_VIEWER_TOOL_NAME = "Skill"

# 会话内保留的 Skill 正文总预算与单个上限（字符）。超过总预算时淘汰最早启用的。
SKILL_CONTEXT_TOTAL_CHARS = 12000
SKILL_CONTEXT_PER_SKILL_CHARS = 6000
# 判定正文是否仍完整在 context 里：头尾各取这么多字符做指纹。
FINGERPRINT_CHARS = 120

REINJECT_HEADER = (
    "以下 Skill 已在本会话中通过 Skill 工具读取并启用，正文重新附上"
    "（上下文压缩、截断或重建后自动恢复）。直接按这些说明执行，不要再次调用 "
    "Skill 工具读取同名 Skill；references/ 与脚本仍按需 Read / 执行。"
)
TRUNCATED_NOTE = "\n\n[正文过长，此处已截断；需要完整内容时按 <agent-skills> 中的 <dir> 用 Read 读取 SKILL.md]"


def _middle_context(agent: Any) -> dict[str, Any] | None:
    state = getattr(agent, "state", None)
    store = getattr(state, "middle_context", None)
    return store if isinstance(store, dict) else None


def activated_skills(agent: Any) -> dict[str, dict[str, Any]]:
    """返回当前实例已启用的 Skill：`{name: {"markdown", "activatedAt"}}`，按启用时间排序。"""

    store = _middle_context(agent)
    if store is None:
        return {}
    raw = store.get(ACTIVATED_SKILLS_KEY)
    if not isinstance(raw, dict):
        return {}
    return dict(sorted(raw.items(), key=lambda item: float(item[1].get("activatedAt") or 0)))


def activate_skill(agent: Any, name: str, markdown: str, *, mark_pending_check: bool = True) -> bool:
    """记录一个已读取的 Skill 正文；返回是否成功写入。"""

    store = _middle_context(agent)
    name = (name or "").strip()
    markdown = (markdown or "").strip()
    if store is None or not name or not markdown:
        return False
    if len(markdown) > SKILL_CONTEXT_PER_SKILL_CHARS:
        markdown = markdown[:SKILL_CONTEXT_PER_SKILL_CHARS].rstrip() + TRUNCATED_NOTE
    current = dict(store.get(ACTIVATED_SKILLS_KEY) or {})
    current[name] = {"markdown": markdown, "activatedAt": time.time()}
    store[ACTIVATED_SKILLS_KEY] = _fit_budget(current)
    if mark_pending_check:
        store[PENDING_CHECK_KEY] = True
    return True


def deactivate_skill(agent: Any, name: str) -> bool:
    store = _middle_context(agent)
    if store is None:
        return False
    current = dict(store.get(ACTIVATED_SKILLS_KEY) or {})
    if name not in current:
        return False
    del current[name]
    store[ACTIVATED_SKILLS_KEY] = current
    return True


def _fit_budget(skills: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    ordered = sorted(skills.items(), key=lambda item: float(item[1].get("activatedAt") or 0))
    total = sum(len(str(entry.get("markdown") or "")) for _, entry in ordered)
    while ordered and total > SKILL_CONTEXT_TOTAL_CHARS:
        name, entry = ordered.pop(0)
        total -= len(str(entry.get("markdown") or ""))
        logger.info("已启用 Skill 正文超出预算，淘汰最早启用的 %s", name)
    return dict(ordered)


def _iter_context_texts(context: Iterable[Any]) -> Iterable[str]:
    for msg in context:
        for block in getattr(msg, "content", None) or []:
            block_type = getattr(block, "type", "")
            if block_type == "text":
                yield str(getattr(block, "text", "") or "")
            elif block_type == "hint":
                hint = getattr(block, "hint", "")
                if isinstance(hint, str):
                    yield hint
                else:
                    for item in hint or []:
                        if getattr(item, "type", "") == "text":
                            yield str(getattr(item, "text", "") or "")
            elif block_type == "tool_result":
                output = getattr(block, "output", "")
                if isinstance(output, str):
                    yield output
                else:
                    for item in output or []:
                        if getattr(item, "type", "") == "text":
                            yield str(getattr(item, "text", "") or "")


def _fingerprints(markdown: str) -> tuple[str, str]:
    body = markdown.split(TRUNCATED_NOTE, 1)[0].strip()
    head = body[:FINGERPRINT_CHARS]
    tail = body[-FINGERPRINT_CHARS:] if len(body) > FINGERPRINT_CHARS else body
    return head, tail


def missing_activated_skills(agent: Any) -> list[str]:
    """返回正文已不在 context 中（被压缩 / 截断 / 重建丢失）的已启用 Skill 名称。"""

    skills = activated_skills(agent)
    if not skills:
        return []
    state = getattr(agent, "state", None)
    context = list(getattr(state, "context", []) or [])
    corpus = "\n".join(_iter_context_texts(context))
    missing: list[str] = []
    for name, entry in skills.items():
        head, tail = _fingerprints(str(entry.get("markdown") or ""))
        if not head or head not in corpus or tail not in corpus:
            missing.append(name)
    return missing


def ensure_activated_skills_in_context(agent: Any) -> list[str]:
    """把缺失的已启用 Skill 正文以 HintBlock 追加回 context，返回补回的名称。"""

    missing = missing_activated_skills(agent)
    store = _middle_context(agent)
    if store is not None:
        store[PENDING_CHECK_KEY] = False
    if not missing:
        return []
    skills = activated_skills(agent)
    sections = [REINJECT_HEADER]
    for name in missing:
        markdown = str(skills[name].get("markdown") or "")
        sections.append(f'<activated-skill name="{name}">\n{markdown}\n</activated-skill>')
    hint = "<system-reminder>\n" + "\n\n".join(sections) + "\n</system-reminder>"
    state = getattr(agent, "state", None)
    context = getattr(state, "context", None)
    if not isinstance(context, list):
        return []
    context.append(AssistantMsg(name=getattr(agent, "name", "assistant"), content=[HintBlock(hint=hint)]))
    logger.info("已重新附上 Skill 正文: %s", ", ".join(missing))
    return missing


def activate_forced_skills(agent: Any, skills: Iterable[Any]) -> list[str]:
    """把用户本轮强制开启的 Skill 正文预写入会话，等同于已经用 Skill 工具读过。"""

    from src.catalog.skills import read_package_markdown

    activated: list[str] = []
    for skill in skills or []:
        name = str(getattr(skill, "name", "") or getattr(skill, "id", "") or "").strip()
        markdown = str(getattr(skill, "content", "") or "").strip()
        resource_path = str(getattr(skill, "resource_path", "") or "")
        if not markdown and resource_path:
            markdown = read_package_markdown(resource_path).strip()
        if not name or not markdown:
            continue
        if activate_skill(agent, name, markdown):
            activated.append(name)
    if activated:
        ensure_activated_skills_in_context(agent)
    return activated


def restore_activated_skills(
    agent: Any,
    *,
    recent_skill_names: Iterable[str],
    available_skills: Iterable[Any],
    limit: int = 3,
) -> list[str]:
    """实例重建后，用持久化的 SkillRun 记录恢复"模型已经读过"的 Skill 正文。

    只恢复仍在本轮 Agent 可用 catalog 中的 Skill（`available_skills` 需带 `name` / `content`），
    最多 `limit` 个、按最近使用倒序；已在 middle_context 里的不重复写。正文本身
    由 middleware 在下一次 `on_reply` 时补进 context。
    """

    if activated_skills(agent):
        return []
    by_name = {
        str(getattr(skill, "name", "") or ""): str(getattr(skill, "content", "") or "")
        for skill in available_skills
    }
    restored: list[str] = []
    now = time.time()
    # recent 是"最近优先"；按启用时间写入时反过来，保证最近使用的最后被淘汰。
    for offset, name in enumerate(reversed(list(recent_skill_names)[:limit])):
        markdown = by_name.get(name, "")
        if not markdown:
            continue
        if activate_skill(agent, name, markdown, mark_pending_check=False):
            store = _middle_context(agent)
            if store is not None:
                store[ACTIVATED_SKILLS_KEY][name]["activatedAt"] = now - (limit - offset)
            restored.append(name)
    return restored


def _skill_viewer_name(agent: Any) -> str:
    toolkit = getattr(agent, "toolkit", None)
    viewer = getattr(toolkit, "builtin_skill_viewer", None)
    tool = getattr(viewer, "tool", None)
    return str(getattr(tool, "name", "") or SKILL_VIEWER_TOOL_NAME)


def _requested_skill_name(raw_input: Any) -> str:
    """`ToolCallBlock.input` 是流式累积的 JSON 字符串；解析出 `skill` 参数。"""

    if isinstance(raw_input, dict):
        value: Any = raw_input
    else:
        try:
            value = json.loads(str(raw_input or "{}"))
        except json.JSONDecodeError:
            return ""
    return str(value.get("skill") or "").strip() if isinstance(value, dict) else ""


def _response_text(response: Any) -> str:
    parts = [
        str(getattr(block, "text", "") or "")
        for block in getattr(response, "content", None) or []
        if getattr(block, "type", "") == "text"
    ]
    return "\n".join(part for part in parts if part)


class SkillContextMiddleware(MiddlewareBase):
    """捕获 Skill 工具输出并在压缩 / 重建后重新附上已启用 Skill 正文。"""

    async def on_reply(
        self,
        agent: Any,
        input_kwargs: dict[str, Any],
        next_handler: Callable[..., AsyncGenerator[Any, None]],
    ) -> AsyncGenerator[Any, None]:
        # 每轮开始先检查：实例重建后 middle_context 里有记录但 context 已经没有正文。
        ensure_activated_skills_in_context(agent)
        async for item in next_handler(**input_kwargs):
            yield item

    async def on_reasoning(
        self,
        agent: Any,
        input_kwargs: dict[str, Any],
        next_handler: Callable[..., AsyncGenerator[Any, None]],
    ) -> AsyncGenerator[Any, None]:
        # 只在刚读过 Skill 之后检查一次：tool result 被 tool_result_limit 截断时，
        # 下一步 reasoning 前就把完整正文补上，而不是等到下一轮。
        store = _middle_context(agent)
        if store is not None and store.get(PENDING_CHECK_KEY):
            ensure_activated_skills_in_context(agent)
        async for item in next_handler(**input_kwargs):
            yield item

    async def on_acting(
        self,
        agent: Any,
        input_kwargs: dict[str, Any],
        next_handler: Callable[..., AsyncGenerator[Any, None]],
    ) -> AsyncGenerator[Any, None]:
        tool_call = input_kwargs.get("tool_call")
        is_skill_viewer = str(getattr(tool_call, "name", "") or "") == _skill_viewer_name(agent)
        last: Any = None
        async for item in next_handler(**input_kwargs):
            last = item
            yield item
        if not is_skill_viewer or last is None:
            return
        # ToolResponse.state 是 StrEnum（success / error / denied / interrupted）。
        if str(getattr(last, "state", "success") or "success").lower() != "success":
            return
        skill_name = _requested_skill_name(getattr(tool_call, "input", None))
        text = _response_text(last)
        if text.startswith("SkillNotFoundError"):
            return
        activate_skill(agent, skill_name, text)

    async def on_compress_context(
        self,
        agent: Any,
        input_kwargs: dict[str, Any],
        next_handler: Callable[..., Awaitable[None]],
    ) -> None:
        state = getattr(agent, "state", None)
        before = (len(getattr(state, "context", []) or []), str(getattr(state, "summary", "") or ""))
        await next_handler(**input_kwargs)
        after = (len(getattr(state, "context", []) or []), str(getattr(state, "summary", "") or ""))
        # compress_context 每次 reasoning 前都会被调用，多数时候低于阈值直接返回；
        # 只有真的压缩过（context / summary 变化）才需要补回被摘要掉的正文。
        if before != after:
            ensure_activated_skills_in_context(agent)
