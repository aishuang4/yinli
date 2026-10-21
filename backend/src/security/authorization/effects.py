"""从平台注册身份分类动作；不信任模型、MCP annotations 或工具只读自述。"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from .models import ActionEffect


@dataclass(frozen=True)
class ToolIdentity:
    """由服务端实际类型构造，不能从调用参数或工具描述反序列化。"""

    module: str
    class_name: str
    name: str
    is_mcp: bool = False


_READ_ONLY = frozenset({
    ("agentscope.tool._builtin._skill", "SkillViewer"),
    ("src.tools.scoped_read", "ScopedRead"),
    ("src.tools.scoped_grep", "ScopedGrep"),
    ("src.tools.scoped_glob", "ScopedGlob"),
    ("src.tools.timeline_memory", "TimelineRetrieve"),
    ("src.tools.scheduled_job_tools", "ListJobs"),
    ("src.tools.delegate_task", "SubagentTaskList"),
    ("src.tools.memory_reflection", "MemoryRead"),
    ("src.tools.scheduled_job_tools", "CheckJobFeasibility"),
    ("src.server.routes.chat.team_runtime.tools", "ListMembers"),
    ("src.server.routes.chat.team_runtime.tools", "ReadTasks"),
    ("src.server.routes.chat.plan_mode.tools", "TaskListTool"),
})
_OUTPUT = frozenset({("src.tools.ask_user", "AskUser"),
                     ("src.group_studio.tools_worker", "GroupPostMessage")})
_MUTATION = frozenset({
    ("agentscope.tool._builtin._meta", "ResetTools"),
    ("src.tools.scoped_write", "ScopedWrite"), ("src.tools.scoped_edit", "ScopedEdit"),
    ("src.tools.timeline_memory", "TimelineIngest"),
    # wait/read 会更新结果投递和消费状态，不因工具名含 Read 自动豁免。
    *(("src.tools.delegate_task", name) for name in
      ("SubagentTaskCancel", "SubagentTaskWait", "SubagentResultRead")),
    *(("src.tools.memory_reflection", name) for name in
      ("MemoryAdd", "MemoryUpdate", "MemoryDelete")),
    ("src.server.routes.chat.team_runtime.tools", "ReadMailbox"),
    *(("src.tools.scheduled_job_tools", name) for name in
      ("ScheduleJob", "PauseJob", "ConfirmJob", "RecheckJobCapabilities")),
})

_ORCHESTRATION = frozenset({
    ("src.tools.delegate_task", "DelegateTask"),
    *(("src.server.routes.chat.team_runtime.tools", name) for name in
      ("SpawnMember", "CreateTask", "SendMail", "FinishCoordination", "SubmitResult")),
    *(("src.server.routes.chat.plan_mode.tools", name) for name in
      ("PlanEnterTool", "PlanWriteTool", "PlanRequestReviewTool", "PlanSetStepsTool",
       "TaskOutputTool", "PlanRequestReplanTool", "PlanCompleteTool")),
})


def is_simple_readonly_shell(command: str) -> bool:
    """仅登记三个可完整解析的模板，脚本、变量与 shell 控制符全部未知。"""

    if not command or re.search(r"[\n\r;&|<>`$\\*?\[\]{}~]", command):
        return False
    try:
        parts = shlex.split(command)
    except ValueError:
        return False
    if parts == ["pwd"]:
        return True
    # 禁止选项注入；路径的 scope 由边界层再次检查。
    if len(parts) == 2 and parts[0] == "cat" and not parts[1].startswith("-"):
        return True
    return (len(parts) == 3 and parts[:2] == ["wc", "-l"]
            and not parts[2].startswith("-"))


def classify_effect(identity: ToolIdentity, inputs: dict[str, Any], *,
                    known_urls: frozenset[str] = frozenset(),
                    shell_readonly_proven: bool = False) -> ActionEffect:
    """默认未知，可信已知 URL 也不能通过 query/凭据外发内容。"""

    key = (identity.module, identity.class_name)
    if identity.is_mcp:
        return "unknown"
    if key in _OUTPUT:
        return "output"
    if key in _READ_ONLY:
        return "read_only"
    if key in {("src.tools.scoped_write", "ScopedWrite"), ("src.tools.scoped_edit", "ScopedEdit")}:
        target = str(inputs.get("file_path") or "").replace("\\", "/")
        # Skill/profile/MCP/HTTP definitions are control-plane resources. Their
        # content revision is checked per action and must not become a global
        # capability version.
        control_markers = ("/.agents/skills/", "/.agent/skills/", "/.claude/skills/",
                           "/.agents/profiles/", "/.agents/mcp/", "/.agents/tools/")
        if any(marker in target for marker in control_markers):
            return "control_plane_mutation"
    if key in _MUTATION:
        return "mutation"
    if key == ("src.tools.web_fetch", "WebFetch"):
        url = str(inputs.get("url") or "")
        parsed = urlsplit(url)
        if (url in known_urls and parsed.scheme in {"http", "https"} and parsed.netloc
                and not parsed.query and not parsed.username and not parsed.password):
            return "read_only"
        return "outbound"
    if key in {("src.tools.http_request", "HttpRequest"),
               ("src.tools.configured_http_tool", "ConfiguredHttpTool"),
               ("src.tools.knowledge_base_search", "KnowledgeBaseSearch")}:
        return "outbound"
    # 此证据仅由平台绑定 cwd、固定可执行文件和最终目标后传入。
    if key == ("src.agents.guarded_bash", "GuardedBash"):
        return "read_only" if shell_readonly_proven else "unknown"
    if key in _ORCHESTRATION:
        return "orchestration"
    return "unknown"
