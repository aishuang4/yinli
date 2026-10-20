"""唯一的 SmartGuard prompt。当前 Run 在冻结时拷走正文，之后改文件只影响新 Run。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

_PROMPT_PATH = Path(__file__).with_name("prompts") / "smartguard.md"


@dataclass(frozen=True)
class SmartGuardPrompt:
    """已审定正文和摘要。"""

    version: str
    digest: str
    system: str
    user_template: str


def load_smartguard_prompt() -> SmartGuardPrompt:
    """读取资源。正文不含文件头。"""

    try:
        raw = _PROMPT_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError("审核提示词资源缺失，桌面端需要重新打包后端后再保存") from exc
    header, _, body = raw.partition("--- system ---")
    version = ""
    for line in header.splitlines():
        if line.startswith("prompt-version:"):
            version = line.split(":", 1)[1].strip()
    system, _, user = body.partition("--- user ---")
    system = system.strip("\n")
    user = user.strip("\n")
    if not version or not system or not user:
        raise ValueError("SmartGuard prompt 资源不完整")
    digest = hashlib.sha256(system.encode("utf-8")).hexdigest()
    return SmartGuardPrompt(version=version, digest=digest, system=system, user_template=user)


def render_smartguard_user(prompt: SmartGuardPrompt, *, trusted_json: str, action: str) -> str:
    """只替换两个占位。动作正文里的花括号保持原样。"""

    return (prompt.user_template
            .replace("{trusted_json}", trusted_json)
            .replace("{action}", action))
