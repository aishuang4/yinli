"""Project API：slug、目录授权，以及不再提供 Workspace。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from src.server.main import app


def test_default_project_is_listed_and_old_workspace_routes_are_gone():
    with TestClient(app) as client:
        _assert_default_project(client)


def _assert_default_project(client: TestClient) -> None:
    listed = client.get("/api/projects")
    assert listed.status_code == 200
    body = listed.json()
    assert body[0]["slug"] == "default"
    assert body[0]["name"] == "默认项目"
    assert client.get("/api/workspaces").status_code == 404
    assert client.post("/api/workspaces/bootstrap", json={"workspaceName": "x", "name": "y", "rootPath": "/tmp"}).status_code == 404


def test_create_archive_and_reject_bad_directories(tmp_path: Path):
    with TestClient(app) as client:
        _assert_project_lifecycle(client, tmp_path)


def _assert_project_lifecycle(client: TestClient, tmp_path: Path) -> None:
    root = tmp_path / "demo"
    root.mkdir()
    created = client.post("/api/projects", json={"name": "演示项目", "primaryPath": str(root)})
    assert created.status_code == 201
    slug = created.json()["slug"]
    assert created.json()["primaryPath"] == str(root.resolve())

    duplicate = client.post("/api/projects", json={"name": "演示项目", "primaryPath": str(root)})
    assert duplicate.status_code == 422

    missing = client.post("/api/projects", json={"name": "不存在", "primaryPath": str(tmp_path / "missing")})
    assert missing.status_code == 422

    link = tmp_path / "link"
    link.symlink_to(root, target_is_directory=True)
    linked = client.post(
        f"/api/projects/{slug}/folders",
        json={"path": str(link), "accessMode": "read"},
    )
    assert linked.status_code == 422

    extra = tmp_path / "extra"
    extra.mkdir()
    added = client.post(
        f"/api/projects/{slug}/folders",
        json={"path": str(extra), "accessMode": "read"},
    )
    assert added.status_code == 201
    assert any(item["path"] == str(extra.resolve()) for item in added.json()["folders"])
    removed = client.request(
        "DELETE",
        f"/api/projects/{slug}/folders",
        json={"path": str(extra.resolve())},
    )
    assert removed.status_code == 200
    assert all(item["path"] != str(extra.resolve()) for item in removed.json()["folders"])
    assert client.request(
        "DELETE",
        f"/api/projects/{slug}/folders",
        json={"path": str(root.resolve())},
    ).status_code == 422

    parent = client.post(
        f"/api/projects/{slug}/folders",
        json={"path": str(root / ".." / "extra"), "accessMode": "read"},
    )
    assert parent.status_code == 422

    patched = client.patch(f"/api/projects/{slug}", json={"color": "#123456"})
    assert patched.status_code == 200
    assert patched.json()["color"] == "#123456"
    assert patched.json()["name"] == "演示项目"

    archived = client.post(f"/api/projects/{slug}/archive")
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"
    assert client.post("/api/projects/default/archive").status_code == 422
    assert client.delete("/api/projects/default").status_code == 422
    assert client.delete(f"/api/projects/{slug}").status_code == 204
    assert client.get(f"/api/projects/{slug}").status_code == 404


def test_hosted_rejects_extra_folder(monkeypatch, tmp_path: Path):
    root = tmp_path / "hosted-demo"
    root.mkdir()
    with TestClient(app) as client:
        created = client.post("/api/projects", json={"name": "托管项目", "primaryPath": str(root)})
        assert created.status_code == 201
        slug = created.json()["slug"]
        monkeypatch.setattr("src.server.routes.projects.is_hosted_mode", lambda: True)
        rejected = client.post(
            f"/api/projects/{slug}/folders",
            json={"path": str(root), "accessMode": "read"},
        )
        assert rejected.status_code == 405
