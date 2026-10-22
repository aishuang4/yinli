"""Job 定义文件与 sqlite 实例同步。"""

from pathlib import Path

import pytest

from src.catalog.job_files import JobFileRecord, parse_job_markdown, write_job_file
from src.catalog.paths import jobs_dir
from src.catalog.sync_jobs import persist_job_definition, sync_job_definitions
from src.scheduler.service import ScheduledJobService
from src.scheduler.settings import set_scheduled_jobs_enabled
from src.storage.engine import database
from src.storage.engine.database import (
    DEFAULT_PROJECT_ID,
    DEFAULT_WORKSPACE_ID,
    ensure_db,
)


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr("src.config.runtime_paths.USER_DATA_ROOT", tmp_path / "user-data")
    ensure_db()
    yield tmp_path
    database.forget_db(database.db_path())


def _paused_record(**overrides) -> JobFileRecord:
    payload = dict(
        id="file-job",
        name="文件任务",
        
        project_id=DEFAULT_PROJECT_ID,
        agent_id="system-websearch-job",
        frozen_prompt="从文件创建。",
        schedule_kind="weekly",
        schedule_expr={"weekdays": ["tue"], "at": "10:00"},
        timezone="Asia/Shanghai",
        desired_status="paused",
        task_intent="生成周报",
    )
    payload.update(overrides)
    return JobFileRecord(**payload)


def test_job_markdown_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "weekly-report.md"
    record = JobFileRecord(
        id="weekly-report",
        name="周报",
        
        project_id="default",
        agent_id="system-websearch-job",
        frozen_prompt="每周生成周报。",
        schedule_kind="weekly",
        schedule_expr={"weekdays": ["mon"], "at": "09:00"},
        timezone="Asia/Shanghai",
        desired_status="paused",
    )
    write_job_file(path, record)
    parsed = parse_job_markdown(
        path.read_text(encoding="utf-8"),
        path=path,
        
        project_id="default",
        source_scope="project",
    )
    assert parsed.id == "weekly-report"
    assert parsed.frozen_prompt == "每周生成周报。"
    assert parsed.schedule_expr["weekdays"] == ["mon"]
    assert parsed.desired_status == "paused"


def test_sync_creates_sqlite_instance_from_project_file(isolated_db, tmp_path) -> None:
    set_scheduled_jobs_enabled(True)
    jobs = jobs_dir()
    jobs.mkdir(parents=True, exist_ok=True)
    write_job_file(jobs / "file-job.md", _paused_record())
    result = sync_job_definitions(project_id=DEFAULT_PROJECT_ID)
    assert result["created"] == 1
    job = ScheduledJobService().get_job("file-job")
    assert job.name == "文件任务"
    assert job.status == "pending_confirmation"
    assert job.frozen_prompt == "从文件创建。"
    persist_job_definition(job)
    written = Path(jobs / "file-job.md")
    assert written.is_file()
    parsed = parse_job_markdown(
        written.read_text(encoding="utf-8"),
        path=written,
        
        project_id=DEFAULT_PROJECT_ID,
        source_scope="project",
    )
    assert parsed.task_intent == "生成周报"
    assert parsed.desired_status == "paused"


def test_sync_activates_desired_status(isolated_db, tmp_path) -> None:
    set_scheduled_jobs_enabled(True)
    jobs = jobs_dir()
    jobs.mkdir(parents=True, exist_ok=True)
    write_job_file(jobs / "file-job.md", _paused_record(desired_status="active"))
    result = sync_job_definitions(project_id=DEFAULT_PROJECT_ID)
    assert result["created"] == 1
    assert result["activated"] == 1
    job = ScheduledJobService().get_job("file-job")
    assert job.status == "active"
    assert job.next_run_at


def test_persist_writes_workspace_catalog_for_service_jobs(isolated_db) -> None:
    set_scheduled_jobs_enabled(True)
    service = ScheduledJobService()
    job = service.create_job(
        
        project_slug=DEFAULT_PROJECT_ID,
        name="服务写入",
        agent_id="system-websearch-job",
        schedule_kind="weekly",
        schedule_expr={"weekdays": ["wed"], "at": "11:00"},
        timezone="Asia/Shanghai",
        frozen_prompt="由服务创建。",
    )
    path = jobs_dir(DEFAULT_WORKSPACE_ID) / f"{job.id}.md"
    assert path.is_file()
    parsed = parse_job_markdown(
        path.read_text(encoding="utf-8"),
        path=path,
        
        project_id=DEFAULT_PROJECT_ID,
        source_scope="workspace",
    )
    assert parsed.name == "服务写入"
    assert parsed.desired_status == "paused"


def test_get_job_hydrates_definition_from_file(isolated_db) -> None:
    set_scheduled_jobs_enabled(True)
    service = ScheduledJobService()
    job = service.create_job(
        
        project_slug=DEFAULT_PROJECT_ID,
        name="原名",
        agent_id="system-websearch-job",
        schedule_kind="weekly",
        schedule_expr={"weekdays": ["thu"], "at": "12:00"},
        timezone="Asia/Shanghai",
        frozen_prompt="旧提示词。",
    )
    path = jobs_dir(DEFAULT_WORKSPACE_ID) / f"{job.id}.md"
    text = path.read_text(encoding="utf-8")
    path.write_text(
        text.replace("原名", "文件已改").replace("旧提示词。", "新提示词。"),
        encoding="utf-8",
    )
    loaded = service.get_job(job.id)
    assert loaded.name == "文件已改"
    assert loaded.frozen_prompt == "新提示词。"
    assert loaded.status == job.status
