"""Chat 顶层 Run 生命周期状态与转换规则。"""

from typing import Literal


RunLifecycleStatus = Literal[
    "running",
    "waiting_user",
    "waiting_permission",
    "waiting_external",
    "completed",
    "failed",
    "cancelled",
    "cancelling",
]

WAITING_RUN_STATUSES = {
    "waiting_user",
    "waiting_permission",
    "waiting_external",
    "waiting",  # 只兼容升级前持久化快照。
}
TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled"}
ACTIVE_RUN_STATUSES = {"running", "cancelling", *WAITING_RUN_STATUSES}


def waiting_status_for_interaction(interaction_type: str, tool_calls: list[dict] | None = None) -> str:
    """根据 AgentScope 交互类型确定顶层 Run 的具体等待状态。"""

    normalized_type = str(interaction_type or "").upper()
    if normalized_type == "REQUIRE_USER_CONFIRM":
        return "waiting_permission"
    if normalized_type == "REQUIRE_EXTERNAL_EXECUTION":
        names = {str(item.get("name") or "") for item in tool_calls or []}
        return "waiting_user" if "AskUser" in names else "waiting_external"
    return "waiting_user"


def is_waiting_run_status(status: str) -> bool:
    """判断状态是否表示 Runtime 已暂停并等待外部输入。"""

    return str(status or "") in WAITING_RUN_STATUSES


def waiting_reason(wait_state: str) -> str:
    """把具体等待状态转换为稳定原因。"""

    return {
        "waiting_user": "user_input_required",
        "waiting_permission": "permission_required",
        "waiting_external": "external_execution_required",
    }.get(str(wait_state or ""), "user_input_required")


def can_transition_run_status(current: str, target: str) -> bool:
    """校验顶层 Run 状态转换，终态一旦写入便不可逆。"""

    current_status = str(current or "running")
    target_status = str(target or current_status)
    if current_status == target_status:
        return True
    if current_status in TERMINAL_RUN_STATUSES:
        return False
    if current_status == "cancelling":
        return target_status in {"cancelled", "failed"}
    if is_waiting_run_status(current_status):
        if current_status == "waiting" and is_waiting_run_status(target_status):
            return True
        return target_status in {"running", "failed", "cancelled", "cancelling"}
    if current_status == "running":
        return (
            target_status in TERMINAL_RUN_STATUSES
            or target_status == "cancelling"
            or is_waiting_run_status(target_status)
        )
    return False


def stage_for_run_status(status: str) -> str:
    """返回顶层 Run 状态对应的稳定中文阶段说明。"""

    return {
        "waiting": "等待人工处理",
        "waiting_user": "等待用户补充信息",
        "waiting_permission": "等待权限确认",
        "waiting_external": "等待外部执行结果",
        "cancelling": "正在取消",
        "completed": "已完成",
        "failed": "失败",
        "cancelled": "已取消",
    }.get(str(status or ""), "准备执行")
