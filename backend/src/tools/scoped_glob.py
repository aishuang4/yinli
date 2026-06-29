"""受控 Glob 工具，避免宽泛模式扫描项目重目录。"""

import os
from typing import ClassVar

from agentscope.message import TextBlock
from agentscope.tool import Glob
from agentscope.tool._response import ToolChunk


class ScopedGlob(Glob):
    """带默认忽略目录和结果上限的 Glob。"""

    ignored_directories: ClassVar[set[str]] = {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".cache",
        "node_modules",
        "dist",
        "build",
        "coverage",
        "data",
        "sessions",
    }
    max_results: ClassVar[int] = 500

    def _is_ignored_directory(self, path: str) -> bool:
        return os.path.basename(os.path.normpath(path)) in self.ignored_directories

    def collect_all(self, current_dir: str, results: list[str]) -> None:
        """递归收集文件时跳过常见重目录。"""

        try:
            for root, dirs, files in os.walk(current_dir):
                dirs[:] = [
                    directory
                    for directory in dirs
                    if directory not in self.ignored_directories
                ]
                for file in files:
                    results.append(os.path.join(root, file))
                    if len(results) >= self.max_results:
                        return
        except (PermissionError, OSError):
            pass

    def match_parts(
        self,
        parts: list[str],
        part_index: int,
        current_dir: str,
        results: list[str],
    ) -> None:
        """匹配路径片段时跳过重目录并在达到上限后停止。"""

        if len(results) >= self.max_results or self._is_ignored_directory(current_dir):
            return
        if part_index >= len(parts):
            return

        part = parts[part_index]
        is_last = part_index == len(parts) - 1

        if part == "**":
            if is_last:
                self.collect_all(current_dir, results)
            else:
                self.match_parts(parts, part_index + 1, current_dir, results)
                try:
                    with os.scandir(current_dir) as entries:
                        for entry in entries:
                            if len(results) >= self.max_results:
                                return
                            if (
                                entry.is_dir(follow_symlinks=False)
                                and not self._is_ignored_directory(entry.path)
                            ):
                                self.match_parts(parts, part_index, entry.path, results)
                except (PermissionError, OSError):
                    pass
            return

        regex = self.glob_part_to_regex(part)
        try:
            with os.scandir(current_dir) as entries:
                for entry in entries:
                    if len(results) >= self.max_results:
                        return
                    if not regex.match(entry.name):
                        continue
                    full_path = entry.path
                    if is_last:
                        if entry.is_file(follow_symlinks=False):
                            results.append(full_path)
                    elif (
                        entry.is_dir(follow_symlinks=False)
                        and not self._is_ignored_directory(full_path)
                    ):
                        self.match_parts(parts, part_index + 1, full_path, results)
        except (PermissionError, OSError):
            pass

    async def __call__(  # type: ignore[override]
        self,
        pattern: str,
        path: str | None = None,
    ) -> ToolChunk:
        """执行受控 Glob，并提示结果可能被截断。"""

        base_dir = path if path else os.getcwd()
        if not os.path.exists(base_dir):
            return ToolChunk(
                content=[TextBlock(text=f"Directory not found: {base_dir}")],
                state="error",
                is_last=True,
            )

        matches = self.glob_match(pattern, base_dir)
        truncated = len(matches) >= self.max_results
        try:
            matches.sort(key=lambda item: os.stat(item).st_mtime, reverse=True)
        except (OSError, FileNotFoundError):
            pass

        if not matches:
            return ToolChunk(
                content=[TextBlock(text=f"No files found matching pattern: {pattern}")],
                state="running",
                is_last=True,
            )

        text = "\n".join(matches[:self.max_results])
        if not truncated:
            return ToolChunk(
                content=[TextBlock(text=text)],
                state="running",
                is_last=True,
            )
        return ToolChunk(
            content=[
                TextBlock(
                    text=(
                        f"{text}\n\n[结果已限制为前 {self.max_results} 条；"
                        "请缩小 path 或 pattern 后继续查询。]"
                    ),
                ),
            ],
            state=chunk.state,
            is_last=chunk.is_last,
        )
