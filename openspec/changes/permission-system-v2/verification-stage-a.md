# 阶段 A 桥接验证记录

验证环境：仓库现有 Python 3.11 虚拟环境，AgentScope **2.0.5**。测试工具仅把参数记入内存，模型只返回固定文本；没有执行 shell、读取用户文件或请求网络。使用真实 Agent / PermissionEngine / Toolkit，不 mock SDK engine。

命令：`cd backend && uv run pytest test/test_authorization_models.py test/test_import_boundaries.py test/test_agentscope_authorization_baseline.py test/test_permission_adapter.py -q`。

结果：**27 passed**，依赖 opentelemetry 有一条既有 DeprecationWarning。

| 接点 | SDK 基线 | 平台原型验证 |
| --- | --- | --- |
| 普通工具 | engine → acting → call | 规范参数与实际执行一致，事件一对 start/end |
| 人工恢复 | ALLOWED 跳过 engine | ALLOWED 重置并再查平台；未变快照恢复一次，改参数重新审批 |
| 并发 | 通用 suggested rule 合并同工具 ASK | ContextVar 每任务隔离，三次动作三张审批，不生成宽泛规则 |
| 拒绝 | 原生 DENIED 结果写入 context | 平台 deny 和人工拒绝均无执行/占位 |
| external | start → submitted → external，不进入 acting | 分发前消费许可，原生结果回流补 end，无本地工具执行 |
| 实际执行前 | acting middleware 可改输入 | 最内层 acting_impl 再构造快照，变更不能领取原许可 |
| reasoning | SDK 原生模型链 | reasoning / reasoning_impl / 并发执行方法继承未替换，真实 fake model 完成 reasoning |
| 生命周期 | SDK context block 可能复制 | 查权威 context 终态，不重发已 finished/submitted 的调用 |

集中接点在 `agents/permission_adapter.py`，只包装 `_execute_tool_call` 和 `_acting_impl`；未复制 ReAct，没有修改 site-packages 或散落 monkey patch。满足阶段 A 接生产的技术前提。

原型尚未由 `agents/factory.py` 安装；测试内存服务不代表生产许可服务。阶段 B 仍需实现租户持久决策、边界校验、目标版本、取消/过期、执行状态收口，以及 external executor 真正启动前的复核。当前结果不能当作全功能上线或模型质量证据。
