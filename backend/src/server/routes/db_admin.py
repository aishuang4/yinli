"""数据库备份、恢复与健康检查。托管模式要求管理员。"""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from src.config.settings import settings
from src.server.auth.routes import require_admin
from src.storage.maintenance import backup, health, recover
from src.storage.sqlite_tx import StorageLocked

router = APIRouter(prefix="/api/db", tags=["数据库维护"])


class RecoverBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    source: str = Field(min_length=1)
    dest: str = ""


def _guard(request: Request) -> None:
    if settings.serve_mode == "hosted":
        require_admin(request)


@router.get("/health")
def read_db_health(request: Request) -> dict:
    _guard(request)
    try:
        return health()
    except StorageLocked as exc:
        raise HTTPException(status_code=503, detail=exc.code) from exc


@router.post("/backup")
def create_db_backup(request: Request) -> dict[str, str]:
    _guard(request)
    try:
        path = backup()
    except StorageLocked as exc:
        raise HTTPException(status_code=503, detail=exc.code) from exc
    return {"path": str(path)}


@router.post("/recover")
def recover_db(body: RecoverBody, request: Request) -> dict[str, str]:
    """恢复到新文件，不替换当前正在使用的库。"""

    _guard(request)
    from pathlib import Path

    try:
        path = recover(Path(body.source), Path(body.dest) if body.dest else None)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="找不到源数据库") from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail="目标文件已存在") from exc
    except StorageLocked as exc:
        raise HTTPException(status_code=503, detail=exc.code) from exc
    return {"path": str(path)}
