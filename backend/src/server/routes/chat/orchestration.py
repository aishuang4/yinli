"""Chat 执行编排兼容入口。"""

from src.server.routes.chat.team_runtime import (
    TeamRuntimeState,
    prepare_leader_with_team_runtime,
)


LeaderPreparationState = TeamRuntimeState
prepare_leader_after_team_workers = prepare_leader_with_team_runtime
