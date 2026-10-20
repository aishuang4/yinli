"""Job 定义文件与 sqlite 实例同步。"""

from pathlib import Path

import pytest

from src.catalog.job_files import JobFileRecord, parse_job_markdown, write_job_file
from src.catalog.paths import jobs_dir
from src.catalog.sync_jobs import sync_job_definitions
from src.config.runtime_paths import session_job_root
from src.scheduler.service import ScheduledJobService
from src.scheduler.settings import set_scheduled_jobs_enabled
from src.storage import database
from src.storage.database import DEFAULT_PROJECT_ID, DEFAULT_WORKSPACE_ID, ensure_db


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
        workspace_id=DEFAULT_WORKSPACE_ID,
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
        workspace_id="default",
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
        workspace_id="default",
        project_id="default",
        source_scope="project",
    )
    assert parsed.id == "weekly-report"
    assert parsed.frozen_prompt == "每周生成周报。"
    assert parsed.schedule_expr["weekdays"] == ["mon"]
    assert parsed.desired_status == "paused"


def test_sync_ignores_project_job_files(isolated_db, tmp_path) -> None:
    set_scheduled_jobs_enabled(True)
    jobs = tmp_path / ".agents" / "jobs"
    jobs.mkdir(parents=True)
    write_job_file(jobs / "file-job.md", _paused_record())
    result = sync_job_definitions(project_root=tmp_path, project_id=DEFAULT_PROJECT_ID)
    assert result["created"] == 0
    assert database.get_scheduled_job("file-job") is None


def test_session_definition_round_trip_and_list_does_not_resync(isolated_db, monkeypatch) -> None:
    set_scheduled_jobs_enabled(True)
    service = ScheduledJobService()
    calls = {"sync": 0}

    def _boom(*_args, **_kwargs):
        calls["sync"] += 1
        raise AssertionError("list_jobs 不得同步 Job 文件")

    monkeypatch.setattr("src.catalog.sync_jobs.sync_job_definitions", _boom)
    job = service.create_job(
        workspace_id=DEFAULT_WORKSPACE_ID,
        project_id=DEFAULT_PROJECT_ID,
        name="服務寫入",
        agent_id="system-websearch-job",
        schedule_kind="weekly",
        schedule_expr={"weekdays": ["wed"], "at": "11:00"},
        timezone="Asia/Shanghai",
        frozen_prompt="由服務創建。",
    )
    owner = database.get_scheduled_job(job.id)["owner_session_id"]
    path = session_job_root(owner, job.id) / "definition.md"
    assert path.is_file()
    assert not (jobs_dir(DEFAULT_WORKSPACE_ID) / f"{job.id}.md").exists()
    parsed = parse_job_markdown(
        path.read_text(encoding="utf-8"),
        path=path,
        workspace_id=DEFAULT_WORKSPACE_ID,
        project_id=DEFAULT_PROJECT_ID,
        source_scope="session",
    )
    assert parsed.name == "服務寫入"
    assert parsed.frozen_prompt == "由服務創建。"
    listed = service.list_jobs(project_id=DEFAULT_PROJECT_ID)
    assert calls["sync"] == 0
    assert listed[0].id == job.id
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("服務寫入", "文件已改").replace("由服務創建。", "新提示詞。"), encoding="utf-8")
    loaded = service.get_job(job.id)
    assert loaded.name == "文件已改"
    assert loaded.frozen_prompt == "新提示詞。"


def test_sync_activates_session_definition(isolated_db) -> None:
    set_scheduled_jobs_enabled(True)
    service = ScheduledJobService()
    created = service.create_job(
        workspace_id=DEFAULT_WORKSPACE_ID,
        project_id=DEFAULT_PROJECT_ID,
        name="文件任务",
        agent_id="system-websearch-job",
        schedule_kind="weekly",
        schedule_expr={"weekdays": ["tue"], "at": "10:00"},
        timezone="Asia/Shanghai",
        frozen_prompt="从文件创建。",
        job_id="file-job",
        persist_file=False,
    )
    owner = database.get_scheduled_job(created.id)["owner_session_id"]
    path = session_job_root(owner, created.id) / "definition.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    write_job_file(path, _paused_record(id=created.id, desired_status="active"))
    result = sync_job_definitions(project_id=DEFAULT_PROJECT_ID)
    assert result["activated"] == 1
    job = service.get_job("file-job")
    assert job.status == "active"
    assert job.next_run_at


def test_create_job_quota_check_does_not_resync_job_files(isolated_db, monkeypatch) -> None:
    """create_job 可由文件同步调用，配额检查不得再进 list_jobs 形成递归。"""

    set_scheduled_jobs_enabled(True)
    monkeypatch.setattr(
        ScheduledJobService,
        "list_jobs",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("create_job 不得调用 list_jobs")),
    )
    job = ScheduledJobService().create_job(
        workspace_id=DEFAULT_WORKSPACE_ID,
        project_id=DEFAULT_PROJECT_ID,
        name="无递归任务",
        agent_id="system-websearch-job",
        schedule_kind="weekly",
        schedule_expr={"weekdays": ["wed"], "at": "11:00"},
        timezone="Asia/Shanghai",
        frozen_prompt="验证无递归。",
    )
    assert job.name == "无递归任务"


def test_get_job_hydrates_definition_from_file(isolated_db) -> None:
    set_scheduled_jobs_enabled(True)
    service = ScheduledJobService()
    job = service.create_job(
        workspace_id=DEFAULT_WORKSPACE_ID,
        project_id=DEFAULT_PROJECT_ID,
        name="原名",
        agent_id="system-websearch-job",
        schedule_kind="weekly",
        schedule_expr={"weekdays": ["thu"], "at": "12:00"},
        timezone="Asia/Shanghai",
        frozen_prompt="旧提示词。",
    )
    owner = database.get_scheduled_job(job.id)["owner_session_id"]
    path = session_job_root(owner, job.id) / "definition.md"
    text = path.read_text(encoding="utf-8")
    path.write_text(
        text.replace("原名", "文件已改").replace("旧提示词。", "新提示词。"),
        encoding="utf-8",
    )
    loaded = service.get_job(job.id)
    assert loaded.name == "文件已改"
    assert loaded.frozen_prompt == "新提示词。"
    assert loaded.status == job.status
