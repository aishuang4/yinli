"""群聊工作室领域包。独立于 Chat Runtime / TeamRun，禁止被 chat handlers import。

职责边界（V2）：

- `models` / `errors`：对外契约与稳定错误码
- `orchestrator`：状态硬规则，不进群、不调模型
- `speaker_queue`：从 mentions 算队列，不解析正文 @
- `scheduler`：只串行点火 Worker，说完停
- `service`：人类 API 编排（进群、拉人、发言、暂停、结束）
- `worker_runtime` / `tools_worker`：工人一轮生成；工具只留 `group_post_message`
- `lifecycle` / `circuit`：启动对账、删除 session、熔断
- `storage/group_studio_records`：SQLite 读写，不写 Chat 表

V1 主持/阶段机/黑板/工作项仍散落在 `host_runtime`、`tools_host`、
`workboard`、`intake`、`blackboard`。新逻辑不要往这些文件加。
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
