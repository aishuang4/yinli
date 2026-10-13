import asyncio
from pathlib import Path

import pytest

from agentscope.permission import PermissionBehavior, PermissionContext
from agentscope.permission._types import PermissionMode

from src.agents.factory import AgentGuardrails, _create_default_toolkit, _resolve_write_tool_base
from src.tools.scoped_edit import ScopedEdit
from src.tools.scoped_grep import ScopedGrep
from src.tools.scoped_write import ScopedWrite


def test_scoped_write_accepts_project_relative_paths(tmp_path: Path, monkeypatch):
    """Write 支持项目相对路径并写入项目根内文件。"""

    monkeypatch.setattr("src.tools.scoped_paths.default_project_root", lambda: tmp_path)

    chunk = asyncio.run(ScopedWrite()(file_path="docs/out.txt", content="hello\n"))

    assert chunk.state != "error"
    assert (tmp_path / "docs" / "out.txt").read_text(encoding="utf-8") == "hello\n"


def test_scoped_write_uses_session_directory_for_relative_outputs(tmp_path: Path):
    """运行时传入 Session 工作目录时，相对输出不落到 Project root。"""

    project_root = tmp_path / "project"
    session_root = project_root / ".agent-workstation" / "sessions" / "session-a"
    session_root.mkdir(parents=True)

    chunk = asyncio.run(ScopedWrite(base_dir=str(session_root))(
        file_path="report.md",
        content="session output\n",
    ))

    assert chunk.state != "error"
    assert (session_root / "report.md").read_text(encoding="utf-8") == "session output\n"
    assert not (project_root / "report.md").exists()


def test_scoped_edit_accepts_project_relative_paths(tmp_path: Path, monkeypatch):
    """Edit 支持项目相对路径并修改项目根内文件。"""

    docs = tmp_path / "docs"
    docs.mkdir()
    target = docs / "out.txt"
    target.write_text("hello\n", encoding="utf-8")
    monkeypatch.setattr("src.tools.scoped_paths.default_project_root", lambda: tmp_path)

    chunk = asyncio.run(ScopedEdit()(
        file_path="docs/out.txt",
        old_string="hello",
        new_string="hi",
    ))

    assert chunk.state != "error"
    assert target.read_text(encoding="utf-8") == "hi\n"


@pytest.mark.parametrize("tool_kind", ["write", "edit", "grep"])
def test_scoped_file_tools_reject_parent_escape(tmp_path: Path, tool_kind: str):
    """写入、编辑和搜索都不能用父目录跳出 scope。"""

    scope = tmp_path / "project"
    scope.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("needle\n", encoding="utf-8")

    with pytest.raises(ValueError, match="outside filesystem scope"):
        if tool_kind == "write":
            asyncio.run(ScopedWrite(scope_dir=str(scope))(file_path="../outside.txt", content="changed"))
        elif tool_kind == "edit":
            asyncio.run(ScopedEdit(scope_dir=str(scope))(
                file_path="../outside.txt",
                old_string="needle",
                new_string="changed",
            ))
        else:
            asyncio.run(ScopedGrep(scope_dir=str(scope))(pattern="needle", path=".."))


def test_scoped_grep_accepts_project_relative_paths(tmp_path: Path, monkeypatch):
    """Grep 支持项目相对 path，不依赖进程 cwd。"""

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "out.txt").write_text("needle\n", encoding="utf-8")
    monkeypatch.setattr("src.tools.scoped_paths.default_project_root", lambda: tmp_path)

    chunk = asyncio.run(ScopedGrep()(pattern="needle", path="docs"))

    assert chunk.state != "error"
    assert "out.txt" in chunk.content[0].text


def test_scoped_grep_falls_back_from_session_workspace_to_project_scope(tmp_path: Path):
    """Grep 的显式 Project 相对目录不应被空 Session cwd 吞掉。"""

    project_root = tmp_path / "project"
    session_root = project_root / ".agent-workstation" / "sessions" / "session-a"
    backend = project_root / "backend"
    session_root.mkdir(parents=True)
    backend.mkdir()
    (backend / "service.py").write_text("needle\n", encoding="utf-8")

    chunk = asyncio.run(ScopedGrep(
        base_dir=str(session_root),
        scope_dir=str(project_root),
    )(pattern="needle", path="backend"))

    assert chunk.state != "error"
    assert "service.py" in chunk.content[0].text


def test_scoped_grep_falls_back_when_ripgrep_is_unavailable(tmp_path: Path, monkeypatch):
    """Windows 未安装 rg.exe 时，Grep 仍应返回项目内匹配内容。"""

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "guide.md").write_text("first line\nNeedle value\nlast line", encoding="utf-8")
    monkeypatch.setattr("src.tools.scoped_grep.shutil.which", lambda _name: None)

    chunk = asyncio.run(ScopedGrep(scope_dir=str(tmp_path))(
        pattern="needle",
        path=str(docs),
        output_mode="content",
        i=True,
    ))

    assert chunk.state.value == "success"
    assert "兼容模式" in chunk.content[0].text
    assert "Needle value" in chunk.content[0].text


def test_write_check_permissions_denies_instead_of_raising_when_base_outside_scope(tmp_path: Path):
    """Team 旧配置把 session 当 Write base、成员目录当 scope，权限检查不得抛错打断 session。"""

    session = tmp_path / "session"
    member_dir = session / "team" / "member"
    session.mkdir()
    member_dir.mkdir(parents=True)
    context = PermissionContext(mode=PermissionMode.ACCEPT_EDITS, working_directories={})
    decision = asyncio.run(ScopedWrite(base_dir=str(session), scope_dir=str(member_dir)).check_permissions(
        {"file_path": "out.md", "content": "x"},
        context,
    ))
    assert decision.behavior == PermissionBehavior.DENY
    assert "outside filesystem scope" in decision.message


def test_write_tool_base_narrows_to_member_write_scope(tmp_path: Path):
    """write_scope 比 session cwd 更窄时，Write/Edit 必须以私有目录为 base。"""

    session = tmp_path / "session"
    member_dir = session / "team" / "member"
    session.mkdir()
    member_dir.mkdir(parents=True)
    assert _resolve_write_tool_base(str(session), str(member_dir)) == str(member_dir.resolve())
    toolkit = _create_default_toolkit(
        guardrails=AgentGuardrails(filesystem_scope=str(tmp_path)),
        workspace_dir=str(session),
        write_scope_dir=str(member_dir),
    )
    tools = [tool for group in toolkit.tool_groups for tool in group.tools]
    write = next(tool for tool in tools if tool.name == "Write")
    edit = next(tool for tool in tools if tool.name == "Edit")
    read = next(tool for tool in tools if tool.name == "Read")
    assert Path(write.base_dir) == member_dir.resolve()
    assert Path(write.scope_dir) == member_dir.resolve()
    assert Path(edit.base_dir) == member_dir.resolve()
    assert Path(read.base_dir) == session.resolve()
