"""Plan Mode 对话式收口。

Plan 不再提供 approve/deny HTTP 审批端点；`plan_exit` 会把计划作为本轮
assistant 最终答案输出，用户通过下一条自然语言消息决定修改或执行计划。
"""
