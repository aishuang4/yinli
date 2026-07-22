"""受控 Edit 工具，支持项目相对路径。"""

from typing import Any

from agentscope.permission import PermissionContext, PermissionDecision, PermissionRule
from agentscope.state import AgentState
from agentscope.tool import Edit
from agentscope.tool._response import ToolChunk

from src.tools.scoped_paths import resolve_workspace_path, resolved_tool_input


class ScopedEdit(Edit):
    """兼容绝对路径与项目相对路径的 Edit。"""

    description: str = (
        "Performs exact string replacements in files. Accepts either an "
        "absolute path or a path relative to the current project workspace."
    )

    def __init__(self, *, base_dir: str | None = None, scope_dir: str | None = None) -> None:
        """指定相对编辑路径的默认目录。"""

        super().__init__()
        self.base_dir = base_dir
        self.scope_dir = scope_dir

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        return await super().check_permissions(
            resolved_tool_input(tool_input, "file_path", base_dir=self.base_dir, scope_dir=self.scope_dir),
            context,
        )

    def match_rule(
        self,
        rule_content: str | None,
        tool_input: dict[str, Any],
    ) -> bool:
        return super().match_rule(
            rule_content,
            resolved_tool_input(tool_input, "file_path", base_dir=self.base_dir, scope_dir=self.scope_dir),
        )

    def generate_suggestions(
        self,
        tool_input: dict[str, Any],
    ) -> list[PermissionRule]:
        return super().generate_suggestions(
            resolved_tool_input(tool_input, "file_path", base_dir=self.base_dir, scope_dir=self.scope_dir),
        )

    async def __call__(  # type: ignore[override]
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
        _agent_state: AgentState | None = None,
    ) -> ToolChunk:
        return await super().__call__(
            file_path=resolve_workspace_path(file_path, base_dir=self.base_dir, scope_dir=self.scope_dir),
            old_string=old_string,
            new_string=new_string,
            replace_all=replace_all,
            _agent_state=_agent_state,
        )
