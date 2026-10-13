"""托管配额：并发 Run 上限，超限上传要删掉已写入的文件。"""

from __future__ import annotations

import asyncio
import io

import pytest
from fastapi import HTTPException
from starlette.datastructures import UploadFile

from src.config.runtime_paths import tenant_root
from src.project_files import save_project_upload
from src.server.routes.chat.background_runs import start_chat_background_run
from src.tenancy.context import TenantContext, use_tenant

pytest_plugins = ["test.hosted_support"]

USER = "usr_" + "f" * 32


def test_active_run_limit_returns_429(hosted_env, monkeypatch):
    monkeypatch.setattr("src.config.settings.settings.tenant_max_active_runs", 1)
    release = asyncio.Event()

    async def hanging():
        release.set()
        await asyncio.Event().wait()
        if False:
            yield None

    async def scenario() -> None:
        with use_tenant(TenantContext.hosted(USER)):
            first = await start_chat_background_run(session_id="s1", factory=hanging)
            await release.wait()
            with pytest.raises(HTTPException) as exc:
                await start_chat_background_run(session_id="s2", factory=hanging)
            assert exc.value.status_code == 429
            assert exc.value.detail["code"] == "quota_exceeded"
            assert exc.value.detail["quota"] == "max_active_runs"
            first.status = "cancelled"

    asyncio.run(scenario())


def test_oversized_upload_is_removed(hosted_env, monkeypatch):
    monkeypatch.setattr("src.config.settings.settings.tenant_upload_mb", 0)

    async def scenario() -> None:
        with use_tenant(TenantContext.hosted(USER)):
            root = tenant_root() / "projects" / "default"
            root.mkdir(parents=True)
            upload = UploadFile(filename="big.txt", file=io.BytesIO(b"hello"))
            with pytest.raises(HTTPException) as exc:
                await save_project_upload(project_root=root, session_id="sess", upload=upload)
            assert exc.value.status_code == 413
            assert exc.value.detail["quota"] == "upload_mb"
            uploads = list(root.rglob("*"))
            assert not any(path.is_file() for path in uploads)

    asyncio.run(scenario())
