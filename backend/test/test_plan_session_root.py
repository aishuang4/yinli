"""Plan 檔寫在 Session root。刷新與舊計畫恢復不讀外部 `.agent-workstation/plans`。"""

import asyncio
import uuid

from src.config.runtime_paths import session_plan_file
from src.server.routes.chat.plan_mode.manager import PlanModeManager
from src.server.routes.chat.plan_mode.tools import PlanWriteTool, TaskOutputTool


def test_plan_file_todo_and_approval_stay_inside_session(tmp_path) -> None:
    session_id = f"session-{uuid.uuid4().hex}"
    manager = PlanModeManager(
        session_id=session_id,
        run_id=f"run-{uuid.uuid4().hex}",
        agent_id="agent-plan",
        agent_name="Agent",
        persist=True,
        requested=True,
    )
    manager.enter(goal="審批計畫", title="Review")
    written = asyncio.run(PlanWriteTool(manager)(markdown="# Review\n\n先寫檔。"))
    assert "plans/" in written.content[0].text
    plan_id = manager.context.plan_id
    session_file = session_plan_file(session_id, plan_id)
    assert session_file.read_text(encoding="utf-8").startswith("# Review")
    assert ".agent-workstation" not in manager.context.current_plan_file
    manager.write_tasks(items=[{"title": "實作"}])
    reviewed = manager.request_review()

    external = tmp_path / "work" / ".agent-workstation" / "plans" / session_id
    external.mkdir(parents=True)
    decoy = external / f"{plan_id}.md"
    decoy.write_text("EXTERNAL-PLAN-SECRET\n", encoding="utf-8")
    session_file.unlink()

    approved = PlanModeManager(
        session_id=session_id,
        run_id=f"run-{uuid.uuid4().hex}",
        agent_id="agent-plan",
        agent_name="Agent",
        persist=True,
        plan_id=reviewed.plan_id,
        action="approve",
    )
    assert "EXTERNAL-PLAN-SECRET" not in approved.context.markdown
    assert session_file.is_file()
    assert session_file.read_text(encoding="utf-8").startswith("# Review")
    assert decoy.read_text(encoding="utf-8") == "EXTERNAL-PLAN-SECRET\n"

    session_file.write_text("# Review\n\n刷新後的正文。\n", encoding="utf-8")
    refreshed = PlanModeManager(
        session_id=session_id,
        run_id=f"run-{uuid.uuid4().hex}",
        agent_id="agent-plan",
        agent_name="Agent",
        persist=True,
        plan_id=plan_id,
    )
    assert "刷新後的正文" in refreshed.context.markdown
    assert "EXTERNAL-PLAN-SECRET" not in refreshed.context.markdown
    output = asyncio.run(TaskOutputTool(approved)(stepIndex=0, status="running", evidence="開始"))
    assert plan_id in output.content[0].text
