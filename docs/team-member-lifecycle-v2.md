# Team Member 长生命周期

> 状态：2026-10-09。未上线。不要按本文改现网。在线基线是 `docs/team-runtime-redesign.md` 和 `docs/architecture-overview.md`「Team Runtime」。

现网是一次性 TeamRun：Leader 动态创建临时 Member 和任务，成员提交后这次执行结束。Mailbox 可以拦住未读提交，并在综合期间触发重新综合。Reviewer、原任务返工、TaskAttempt 和跨进程恢复都没有做。

若以后要做，目标是：Member 在整个 TeamRun 内存活，但不要常驻模型进程；一次执行是 TaskAttempt；候选结果经 Reviewer 接受后任务才成功。在那之前，不要把这些对象补进 Chat SSE 或恢复持久 Team。
