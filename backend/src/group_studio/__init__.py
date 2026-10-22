"""群聊工作室领域包。独立于 Chat Runtime / TeamRun，禁止被 chat handlers import。

职责边界（V2）：

- `models` / `errors`：对外契约与稳定错误码
- `orchestrator`：状态硬规则，不进群、不调模型
- `speaker_queue`：从 mentions 算队列，不解析正文 @
- `scheduler`：只串行点火 Worker，说完停
- `rooms`：进群、读取、暂停、结束、置顶和改模型
- `members`：拉人、静音、移出
- `speaking`：人类发言、打断、重跑和系统提示
- `artifacts` / `files`：产物登记和会话目录
- `worker_spec`：构造 Worker。`worker_runtime` / `tools_worker`：跑一轮；工具只留 `group_post_message`
- `lifecycle` / `circuit`：启动对账、删除 session、熔断
- `storage/records/group_studio_records`：SQLite 读写，不写 Chat 表

V1 主持/阶段机模块（`host_runtime`、`tools_host`、`workboard`、
`intake`、`blackboard`）已删除，不要恢复。
"""

from src.group_studio.models import GROUP_STUDIO_HOST_AGENT_ID
from src.group_studio.settings import (
    get_group_studio_settings_public,
    is_group_studio_enabled,
    set_group_studio_settings,
)

__all__ = [
    "GROUP_STUDIO_HOST_AGENT_ID",
    "get_group_studio_settings_public",
    "is_group_studio_enabled",
    "set_group_studio_settings",
]
