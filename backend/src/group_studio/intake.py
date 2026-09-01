"""Intake 确认短语分类。人类是离开 drafting 的唯一开关。"""

from __future__ import annotations

import re
from enum import Enum

from src.group_studio.models import GroupStudio

INTAKE_CONFIRM_RE = re.compile(
    r"^(你接手|开始|就这样|确认并开始|可以开始|就这么定|"
    r"开始讨论|开始群聊|我们开始吧|就这么开始)$"
)


class IntakePhraseResult(str, Enum):
    """确认短语相对当前任务卡的分类。"""

    confirmed = "confirmed"
    blocked_no_flag = "blocked_no_flag"
    blocked_no_goal = "blocked_no_goal"
    not_a_phrase = "not_a_phrase"


def classify_intake_phrase(body: str) -> bool:
    """去首尾空白与句末标点后是否命中冻结确认短语。"""

    text = body.strip().rstrip("。.!！")
    return bool(INTAKE_CONFIRM_RE.fullmatch(text))


def maybe_confirm_intake(studio: GroupStudio, body: str) -> IntakePhraseResult:
    """POST /messages 与确认按钮共用的分类，禁止两套 if。"""

    if studio.status != "drafting" or not classify_intake_phrase(body):
        return IntakePhraseResult.not_a_phrase
    if not studio.awaiting_intake_confirm:
        return IntakePhraseResult.blocked_no_flag
    if not studio.task_card.goal.strip():
        return IntakePhraseResult.blocked_no_goal
    return IntakePhraseResult.confirmed
