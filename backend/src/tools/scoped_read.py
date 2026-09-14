"""受控 Read 工具，支持项目相对路径。"""

from typing import Any

from agentscope.state import AgentState
from agentscope.tool import Read
from agentscope.tool._response import ToolChunk

from src.tools.scoped_paths import resolve_existing_workspace_path


class ScopedRead(Read):
    """只读取 filesystem scope 内文件的 Read。"""

    description: str = (
        "Reads a file within the current project filesystem scope. Accepts an "
        "in-scope absolute path or a path relative to the current workspace."
    )

    def __init__(
        self,
        *,
        base_dir: str | None = None,
        scope_dir: str | None = None,
        extra_scopes: list[str] | None = None,
    ) -> None:
        super().__init__()
        self.base_dir = base_dir
        self.scope_dir = scope_dir
        self.extra_scopes = extra_scopes or []

    async def __call__(  # type: ignore[override]
        self,
        file_path: str,
        offset: int = 1,
        limit: int = 2000,
        _agent_state: AgentState | None = None,
        **kwargs: Any,
    ) -> ToolChunk:
        """读取文件；相对路径按项目根目录解析。"""

        resolved_path = self._resolve_file_path(file_path)
        return await super().__call__(
            file_path=resolved_path,
            offset=offset,
            limit=limit,
            _agent_state=_agent_state,
        )

    def _resolve_file_path(self, file_path: str) -> str:
        return resolve_existing_workspace_path(
            file_path,
            base_dir=self.base_dir,
            scope_dir=self.scope_dir,
            extra_scopes=self.extra_scopes,
        )
