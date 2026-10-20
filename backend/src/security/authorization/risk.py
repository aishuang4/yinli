"""确定性风险分类。分类失败一律 unknown，不能因此放行。"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path

from .models import ActionSnapshot, RiskClass

_PLATFORM_INTERNAL = frozenset({
    "PlanEnterTool", "PlanWriteTool", "PlanRequestReviewTool", "PlanSetStepsTool",
    "TaskOutputTool", "PlanRequestReplanTool", "PlanCompleteTool",
    "SpawnMember", "CreateTask", "SendMail", "FinishCoordination", "SubmitResult", "ReadMailbox",
    "DelegateTask", "SubagentTaskWait", "SubagentResultRead", "SubagentTaskCancel", "ResetTools",
})
_LOW_RISK = frozenset({
    "MemoryAdd", "MemoryUpdate", "TimelineIngest", "SkillDraftPropose",
    "ScheduleJob", "PauseJob", "RecheckJobCapabilities", "KnowledgeBaseSearch",
})
_HUMAN_ONLY = frozenset({"ConfirmJob"})
_DANGEROUS_TOOLS = frozenset({
    "MemoryDelete", "HttpRequest", "ConfiguredHttpTool",
})
_CONTROL_MARKERS = (
    "/.agents/skills/", "/.agent/skills/", "/.claude/skills/",
    "/.agents/profiles/", "/.agents/mcp/", "/.agents/tools/",
)
_SECRET_MARKERS = (".env", "credentials", "secret", "models.json", ".pem", "id_rsa")
_DANGEROUS_SHELL = (
    (r"(?:^|[\s;|&])rm\s+-[^\n]*r", "recursive_delete", "递归删除"),
    (r"(?:^|[\s;|&])sudo(?:\s|$)", "sudo", "提权执行"),
    (r"git\s+push\b[^\n]*--force|git\s+push\b[^\n]*-f\b", "force_push", "强制推送"),
    (r"\|\s*(?:ba)?sh\b|\|\s*zsh\b", "pipe_to_shell", "管道送入 shell"),
    (r"chmod\s+777\b", "chmod_777", "放开文件权限"),
    (r">\s*/(?:dev|etc|System|usr|bin|sbin|var)\b", "system_path_write", "写入系统路径"),
)
_SELF_TERMINATION = (
    (r"\blaunchctl\b", "launchctl", "结束或重启本机服务管理项"),
    (r"\b(?:pkill|killall)\b[^\n]*(?:uvicorn|electron|agent-workstation)", "kill_app", "结束本应用进程"),
    (r"\bsystemctl\b[^\n]*\b(?:stop|restart|kill)\b", "systemctl_stop", "停止或重启服务"),
)


@dataclass(frozen=True)
class RiskClassification:
    """分类结果。rule 会进入审核可信区，不包含动作正文。"""

    risk_class: RiskClass
    rule_key: str
    rule_description: str


def classify_risk(action: ActionSnapshot) -> RiskClassification:
    """只读服务端快照。任何异常都归 unknown。"""

    try:
        return _classify(action)
    except Exception:
        return RiskClassification("unknown", "classification_error", "风险分类失败，按不确定处理")


def _classify(action: ActionSnapshot) -> RiskClassification:
    name = action.tool_class or action.tool_name
    if action.effect in {"read_only", "output"}:
        return RiskClassification("trusted_readonly", "trusted_readonly", "平台证明的只读或回复输出")
    if name in _PLATFORM_INTERNAL or action.effect == "orchestration":
        return RiskClassification("platform_internal", f"platform_internal:{name}", "只改变平台协调状态")
    if _touches_control_plane(action) or _writes_app_config(action):
        return RiskClassification("human_only", "control_plane", "控制面或应用配置修改必须人工批准")
    if name in _HUMAN_ONLY:
        return RiskClassification("human_only", f"human_only:{name}", "此动作永不自动批准")
    if _reads_secret_and_sends(action):
        return RiskClassification("human_only", "secret_exfiltration", "读取凭据并外发必须人工批准")
    shell = _shell_risk(action)
    if shell is not None:
        return shell
    if name in _DANGEROUS_TOOLS or (name == "WebFetch" and action.effect == "outbound"):
        return RiskClassification("dangerous", f"dangerous:{name}", "删除或外发")
    if name in {"ScopedWrite", "ScopedEdit"}:
        return _write_risk(action)
    if name in _LOW_RISK:
        return RiskClassification("low_risk", f"low_risk:{name}", "登记的窄写入或固定查询")
    if action.effect == "control_plane_mutation":
        return RiskClassification("human_only", "control_plane", "控制面修改必须人工批准")
    if action.effect == "outbound":
        return RiskClassification("dangerous", "outbound", "外发")
    if not action.targets and action.effect == "unknown" and name == "GuardedBash":
        return RiskClassification("insufficient_evidence", "shell_unparsed", "Shell 目标无法解析")
    return RiskClassification("unknown", f"unknown:{name or 'tool'}", "未登记或语义未确定的动作")


def _touches_control_plane(action: ActionSnapshot) -> bool:
    if action.effect == "control_plane_mutation":
        return True
    return any(marker in target.target.replace("\\", "/") for target in action.targets for marker in _CONTROL_MARKERS)


def _writes_app_config(action: ActionSnapshot) -> bool:
    for target in action.targets:
        text = target.target.replace("\\", "/")
        if "/.agent-workstation/" in text and "/sessions/" not in text:
            return True
    return False


def _write_risk(action: ActionSnapshot) -> RiskClassification:
    if not action.targets:
        return RiskClassification("insufficient_evidence", "write_target_missing", "写入目标无法解析")
    session_id = action.authorization.session_id
    inside = []
    for target in action.targets:
        located = _inside_session(target.target, session_id)
        if located is None:
            return RiskClassification("insufficient_evidence", "write_target_unresolved", "写入目标无法解析")
        inside.append(located)
    if all(inside):
        return RiskClassification("low_risk", "session_write", "Session 根内的非控制面写入")
    return RiskClassification("dangerous", "write_outside_session", "写入 Session 根之外")


def _inside_session(raw: str, session_id: str) -> bool | None:
    text = str(raw or "").strip()
    if not text or not session_id:
        return None
    path = Path(text).expanduser()
    if not path.is_absolute():
        return None
    try:
        resolved = path.resolve()
    except OSError:
        return None
    for parent in (resolved, *resolved.parents):
        if (
            parent.name == session_id
            and parent.parent.name == "sessions"
            and parent.parent.parent.name == ".agent-workstation"
        ):
            return True
    return False


def _reads_secret_and_sends(action: ActionSnapshot) -> bool:
    if action.effect != "outbound" and action.tool_class != "GuardedBash":
        return False
    blob = " ".join(target.target for target in action.targets) + " " + action.normalized_input_json
    lowered = blob.lower()
    has_secret = any(marker in lowered for marker in _SECRET_MARKERS)
    sends = action.effect == "outbound" or bool(re.search(r"https?://", blob))
    return has_secret and sends


def _shell_risk(action: ActionSnapshot) -> RiskClassification | None:
    if action.tool_class != "GuardedBash" and action.tool_name != "GuardedBash":
        return None
    command = _command(action)
    if command is None:
        return RiskClassification("insufficient_evidence", "shell_unparsed", "Shell 无法解析，不交给模型猜测")
    for pattern, key, description in _SELF_TERMINATION:
        if re.search(pattern, command):
            return RiskClassification("human_only", key, description)
    for pattern, key, description in _DANGEROUS_SHELL:
        if re.search(pattern, command):
            return RiskClassification("dangerous", key, description)
    return RiskClassification("unknown", "shell_unclassified", "未证明只读的 Shell")


def _command(action: ActionSnapshot) -> str | None:
    import json

    try:
        payload = json.loads(action.normalized_input_json or "{}")
    except json.JSONDecodeError:
        return None
    command = payload.get("command")
    if not isinstance(command, str) or not command.strip():
        return None
    try:
        shlex.split(command)
    except ValueError:
        return None
    return command
