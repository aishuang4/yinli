"""受控 Read 工具，支持项目相对路径。"""

from typing import Any

from agentscope.state import AgentState
from agentscope.tool import Read
from agentscope.tool._response import ToolChunk

from src.tools.scoped_paths import resolve_workspace_path


class ScopedRead(Read):
    """兼容绝对路径与项目相对路径的 Read。"""

    description: str = (
        "Reads a file from the local filesystem. Accepts either an absolute "
        "path or a path relative to the current project workspace."
    )

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
        return resolve_workspace_path(file_path)
