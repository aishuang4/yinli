"""Team Runtime 启动恢复和文件保留维护。"""

import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.server.routes.chat.store import _append_assistant_message
from src.server.routes.chat.team_runtime.store import TEAM_RUNTIME_ROOT, TeamStore, TeamStoreError
from src.storage.database import (
    get_chat_run,
    update_chat_run_status,
)


INTERRUPTED_REASON = "TeamRun 因后端重启中断，请重新发起本轮协作。"


def maintain_team_runtime_runs(
    *,
    root: Path = TEAM_RUNTIME_ROOT,
    retention_days: int = 30,
    max_records: int = 200,
    now: datetime | None = None,
) -> dict[str, int]:
    """收口孤儿运行并清理超过保留策略的终态目录。"""

    current_time = now or datetime.now(timezone.utc)
    root.mkdir(parents=True, exist_ok=True)
    terminal: list[tuple[datetime, Path]] = []
    interrupted = 0
    skipped = 0

    for path in sorted(root.iterdir()):
        if not path.is_dir():
            continue
        try:
            store = TeamStore.load(path.name, root=root)
            run = store.run()
            if run.get("status") == "running":
                store.cancel_run(INTERRUPTED_REASON)
                _persist_interrupted_run(store)
                interrupted += 1
                run = store.run()
            terminal.append((_run_timestamp(run, current_time), path))
        except (OSError, ValueError, TeamStoreError):
            skipped += 1

    cutoff = current_time - timedelta(days=max(1, retention_days))
    removable = {path for timestamp, path in terminal if timestamp < cutoff}
    survivors = sorted(
        [(timestamp, path) for timestamp, path in terminal if path not in removable],
        key=lambda item: item[0],
        reverse=True,
    )
    for _, path in survivors[max(1, max_records):]:
        removable.add(path)
    for path in removable:
        shutil.rmtree(path, ignore_errors=True)

    return {"interrupted": interrupted, "removed": len(removable), "skipped": skipped}


def delete_team_runs_for_session(session_id: str, *, root: Path = TEAM_RUNTIME_ROOT) -> int:
    """删除 Chat session 对应的终态 TeamRun 文件。"""

    removed = 0
    if not root.exists():
        return removed
    for path in list(root.iterdir()):
        if not path.is_dir():
            continue
        try:
            store = TeamStore.load(path.name, root=root)
            run = store.run()
        except (OSError, ValueError, TeamStoreError):
            continue
        if str(run.get("sessionId") or "") != session_id:
            continue
        if run.get("status") == "running":
            raise TeamStoreError("当前会话仍有运行中的 TeamRun，不能删除")
        shutil.rmtree(path)
        removed += 1
    return removed


def _persist_interrupted_run(store: TeamStore) -> None:
    run = store.run()
    chat_run_id = str(run.get("chatRunId") or "")
    completed_at = str(run.get("completedAt") or datetime.now(timezone.utc).isoformat())
    if chat_run_id:
        update_chat_run_status(chat_run_id, "cancelled", completed_at)
    record = get_chat_run(chat_run_id) if chat_run_id else None
    _append_assistant_message(
        session_id=str(run.get("sessionId") or ""),
        agent_id=str((record or {}).get("agentId") or ""),
        agent_name=str((record or {}).get("agentName") or "Team Leader"),
        model_name="",
        content="",
        error=INTERRUPTED_REASON,
    )


def _run_timestamp(run: dict, fallback: datetime) -> datetime:
    raw = str(run.get("completedAt") or run.get("updatedAt") or run.get("createdAt") or "")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return fallback
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
