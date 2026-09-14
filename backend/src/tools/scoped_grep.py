"""受控 Grep 工具，默认在项目根内搜索，并兼容缺少 ripgrep 的 Windows 环境。"""

import fnmatch
import os
import re
import shutil
from typing import Any, Literal

from agentscope.message import TextBlock, ToolResultState
from agentscope.tool import Grep, ToolChunk

from src.tools.scoped_paths import resolve_existing_workspace_path


class ScopedGrep(Grep):
    """把 Grep 的默认搜索路径和相对 path 收窄到项目根。"""

    _fallback_ignored_directories = {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        ".venv",
        "__pycache__",
    }
    _fallback_max_file_size = 5 * 1024 * 1024
    _type_extensions = {
        "py": {".py"},
        "js": {".js", ".mjs", ".cjs"},
        "ts": {".ts", ".tsx"},
        "json": {".json"},
        "md": {".md", ".markdown"},
        "yaml": {".yaml", ".yml"},
        "xml": {".xml"},
    }

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
        resolved_path = resolve_existing_workspace_path(
            path or ".",
            base_dir=self.base_dir,
            scope_dir=self.scope_dir,
            extra_scopes=self.extra_scopes,
        )
        if shutil.which("rg") is None:
            return self._python_fallback(
                pattern=pattern,
                path=resolved_path,
                output_mode=output_mode,
                glob=glob,
                file_type=type, case_insensitive=i or case_insensitive, context=context,
                multiline=multiline, head_limit=head_limit, offset=offset, include_line_numbers=n,
            )
        return await super().__call__(
            pattern=pattern,
            path=resolved_path,
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

    def _python_fallback(
        self, *, pattern: str, path: str, output_mode: str, glob: str | None,
        file_type: str | None, case_insensitive: bool, context: int | None,
        multiline: bool, head_limit: int | None, offset: int, include_line_numbers: bool,
    ) -> ToolChunk:
        """在未安装 ``rg`` 时提供常用搜索能力，避免 Windows 基础功能不可用。"""

        try:
            flags = re.IGNORECASE if case_insensitive else 0
            expression = re.compile(pattern, flags | (re.DOTALL if multiline else 0))
        except re.error as exc:
            return ToolChunk(
                content=[TextBlock(text=f"Invalid search pattern: {exc}")],
                state=ToolResultState.ERROR,
                is_last=True,
            )

        results: list[str] = []
        for file_path in self._fallback_files(path, glob=glob, file_type=file_type):
            try:
                if os.path.getsize(file_path) > self._fallback_max_file_size:
                    continue
                with open(file_path, "r", encoding="utf-8", errors="ignore") as file:
                    text = file.read()
            except OSError:
                continue
            lines = text.splitlines()
            if multiline:
                if expression.search(text):
                    results.append(file_path)
                continue
            matches = [index for index, line in enumerate(lines) if expression.search(line)]
            if not matches:
                continue
            if output_mode == "files_with_matches":
                results.append(file_path)
            elif output_mode == "count":
                results.append(f"{file_path}:{len(matches)}")
            else:
                for index in self._context_line_indexes(matches, len(lines), context):
                    line_number = f"{index + 1}:" if include_line_numbers else ""
                    results.append(f"{file_path}:{line_number}{lines[index]}")

        if not results:
            return ToolChunk(
                content=[TextBlock(text=f"No matches found for pattern: {pattern}")],
                state=ToolResultState.SUCCESS,
                is_last=True,
            )
        limited, applied_limit = self._apply_head_limit(results, head_limit, offset)
        suffix = ""
        if applied_limit is not None:
            suffix = f"\n\n[Showing results with pagination = limit: {applied_limit}" + (f", offset: {offset}" if offset else "") + "]"
        return ToolChunk(
            content=[
                TextBlock(
                    text="[兼容模式] 未检测到 ripgrep (rg)，已使用内置 Python 搜索。\n\n"
                    + "\n".join(limited)
                    + suffix,
                ),
            ],
            state=ToolResultState.SUCCESS,
            is_last=True,
        )

    def _fallback_files(self, path: str, *, glob: str | None, file_type: str | None) -> list[str]:
        """枚举 Python 降级搜索的可读文件，跳过常见重目录。"""

        if os.path.isfile(path):
            candidates = [path]
        elif os.path.isdir(path):
            candidates = []
            for root, directories, files in os.walk(path):
                directories[:] = [item for item in directories if item not in self._fallback_ignored_directories]
                candidates.extend(os.path.join(root, filename) for filename in files)
        else:
            return []
        extensions = self._type_extensions.get((file_type or "").lower())
        return sorted(
            candidate for candidate in candidates
            if (not extensions or os.path.splitext(candidate)[1].lower() in extensions)
            and self._matches_glob(candidate, glob)
        )

    @staticmethod
    def _matches_glob(path: str, glob: str | None) -> bool:
        if not glob:
            return True
        normalized = path.replace("\\", "/")
        patterns = [item.strip() for item in re.split(r"[\s,]+", glob) if item.strip()]
        return any(fnmatch.fnmatch(normalized, f"*{item.lstrip('./')}") for item in patterns)

    @staticmethod
    def _context_line_indexes(matches: list[int], line_count: int, context: int | None) -> list[int]:
        radius = max(0, int(context or 0))
        return sorted(
            {
                index
                for match in matches
                for index in range(
                    max(0, match - radius),
                    min(line_count, match + radius + 1),
                )
            },
        )

    @staticmethod
    def _apply_head_limit(
        results: list[str],
        head_limit: int | None,
        offset: int,
    ) -> tuple[list[str], int | None]:
        """应用与原生 Grep 一致的基础分页语义。"""

        normalized_offset = max(0, offset)
        remaining = results[normalized_offset:]
        if head_limit is None or head_limit <= 0:
            return remaining, None
        return remaining[:head_limit], head_limit
