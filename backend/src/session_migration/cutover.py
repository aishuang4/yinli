"""切換門。檢查失敗時不寫 complete，並保留舊讀路徑。"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any


def attempt_cutover(
    *,
    ready: bool,
    pause: Callable[[], None],
    resume: Callable[[], None],
    marker: Path,
    switch_reads: Callable[[], None],
) -> dict[str, Any]:
    """先暫停，再驗證。marker 或切換失敗時不把讀路徑改成 Session。"""

    pause()
    try:
        if not ready:
            return {"cutover": False, "readPath": "legacy"}
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps({"ok": True}, ensure_ascii=False), encoding="utf-8")
        try:
            switch_reads()
        except Exception as exc:
            marker.unlink(missing_ok=True)
            return {"cutover": False, "readPath": "legacy", "error": exc.__class__.__name__}
        return {"cutover": True, "readPath": "session"}
    finally:
        resume()
