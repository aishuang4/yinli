"""受控 Edit 工具，支持项目相对路径。"""

from typing import Any

from agentscope.permission import PermissionBehavior, PermissionContext, PermissionDecision, PermissionRule
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

    def __init__(
        self,
        *,
        base_dir: str | None = None,
        scope_dir: str | None = None,
        extra_scopes: list[str] | None = None,
    ) -> None:
        """指定相对编辑路径的默认目录。"""

        super().__init__()
        self.base_dir = base_dir
        self.scope_dir = scope_dir
        self.extra_scopes = extra_scopes or []

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        try:
            resolved = resolved_tool_input(
                tool_input, "file_path",
                base_dir=self.base_dir, scope_dir=self.scope_dir, extra_scopes=self.extra_scopes,
            )
        except ValueError as exc:
            # 越界必须变成拒绝，不能抛到 AgentScope permission 层，否则整个 Member session 会被打断。
            return PermissionDecision(
                behavior=PermissionBehavior.DENY,
                message=str(exc),
                decision_reason="Path is outside filesystem scope",
                bypass_immune=True,
            )
        return await super().check_permissions(resolved, context)

    async def match_rule(
        self,
        rule_content: str | None,
        tool_input: dict[str, Any],
    ) -> bool:
        try:
            resolved = resolved_tool_input(
                tool_input, "file_path",
                base_dir=self.base_dir, scope_dir=self.scope_dir, extra_scopes=self.extra_scopes,
            )
        except ValueError:
            return False
        return await super().match_rule(rule_content, resolved)

    async def generate_suggestions(
        self,
        tool_input: dict[str, Any],
    ) -> list[PermissionRule]:
        try:
            resolved = resolved_tool_input(
                tool_input, "file_path",
                base_dir=self.base_dir, scope_dir=self.scope_dir, extra_scopes=self.extra_scopes,
            )
        except ValueError:
            return []
        return await super().generate_suggestions(resolved)

    async def __call__(  # type: ignore[override]
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
        _agent_state: AgentState | None = None,
    ) -> ToolChunk:
        return await super().__call__(
            file_path=resolve_workspace_path(
                file_path, base_dir=self.base_dir, scope_dir=self.scope_dir, extra_scopes=self.extra_scopes,
            ),
            old_string=old_string,
            new_string=new_string,
            replace_all=replace_all,
            _agent_state=_agent_state,
        )
