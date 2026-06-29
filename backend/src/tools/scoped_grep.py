"""受控 Grep 工具，默认在项目根内搜索。"""

from typing import Any, Literal

from agentscope.tool import Grep
from agentscope.tool._response import ToolChunk

from src.agents.runtime_types import WORKSPACE_DIR
from src.tools.scoped_paths import resolve_workspace_path


class ScopedGrep(Grep):
    """把 Grep 的默认搜索路径和相对 path 收窄到项目根。"""

    async def __call__(  # type: ignore[override]
        self,
        pattern: str,
        path: str | None = None,
        output_mode: Literal[
            "content",
            "files_with_matches",
            "count",
        ] = "files_with_matches",
        glob: str | None = None,
        type: str | None = None,
        i: bool = False,
        case_insensitive: bool = False,
        context: int | None = None,
        multiline: bool = False,
        head_limit: int | None = None,
        offset: int = 0,
        n: bool = True,
        **kwargs: Any,
    ) -> ToolChunk:
        return await super().__call__(
            pattern=pattern,
            path=resolve_workspace_path(path) if path else WORKSPACE_DIR,
            output_mode=output_mode,
            glob=glob,
            type=type,
            i=i,
            case_insensitive=case_insensitive,
            context=context,
            multiline=multiline,
            head_limit=head_limit,
            offset=offset,
            n=n,
            **kwargs,
        )
