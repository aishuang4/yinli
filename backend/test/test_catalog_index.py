"""INDEX.md 生成。"""

from src.catalog.index import refresh_project_index
from src.catalog.loader import invalidate_catalog, save_agent
from src.catalog.agent_models import AgentResponse
from src.catalog.system_defaults import utc_now


def test_index_contains_required_headings(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("src.config.runtime_paths.USER_DATA_ROOT", tmp_path)
    invalidate_catalog()
    save_agent(AgentResponse.model_validate({
        "id": "writer",
        "name": "写手",
        "avatar": "",
        "description": "",
        "system_prompt": "写",
        "modelConfig": {},
        "mcpTools": [],
        "knowledgeTools": [],
        "skills": [],
        "tags": [],
        "guardrails": {},
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "workspaceId": "default",
        "role": "chat",
        "jobToolkit": {},
    }))
    project = tmp_path / "project"
    project.mkdir()
    path = refresh_project_index(project, workspace_id="default", project_id="p1")
    assert path is None
    assert not (project / ".agents").exists()


def test_index_records_warnings_and_job_parse_errors(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("src.config.runtime_paths.USER_DATA_ROOT", tmp_path)
    invalidate_catalog()
    from src.catalog.paths import profile_path

    path = profile_path("default", "writer")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\nid: writer\nname: 写手\nrole: chat\nweird_key: 1\n---\n\n写\n",
        encoding="utf-8",
    )
    project = tmp_path / "project"
    project.mkdir()
    out = refresh_project_index(project, workspace_id="default", project_id="p1")
    assert out is None
    assert not (project / ".agents" / "INDEX.md").exists()
