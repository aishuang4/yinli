"""Team 单任务活动执行预算；人工等待暂停计时，恢复沿用剩余预算。"""

import asyncio


class ActiveExecutionBudget:
    """用事件循环 deadline 驱动超时，不用墙钟轮询计算人工等待。"""

    def __init__(self, seconds: float) -> None:
        self.remaining = seconds
        self.timeout: asyncio.Timeout | None = None

    async def __aenter__(self):
        self.timeout = asyncio.timeout(self.remaining)
        await self.timeout.__aenter__()
        return self

    async def __aexit__(self, *args):
        return await self.timeout.__aexit__(*args)

    async def wait_for_human(self, waiter, interaction):
        """只排除真实人工等待，事件发布与工具执行仍消耗预算。"""

        loop = asyncio.get_running_loop()
        self.remaining = max(0, self.timeout.when() - loop.time())
        self.timeout.reschedule(None)
        try:
            return await waiter(interaction)
        finally:
            if not self.timeout.expired():
                self.timeout.reschedule(loop.time() + self.remaining)
