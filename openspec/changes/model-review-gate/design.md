# Design

## Context

见 `proposal.md`。现行自动触发有两道关键词关口：`admission.py` 的技能分用「以后 / 记住 / 实现 / skill」等标记抬分，`skill_evolution.evaluate_skill_evolution_router_gate` 再用「技能 / 流程 / 复用」等标记否决。`reflection.py` 先看准入的 `should_evaluate_skill`，再看 router gate，两道都过才调用 `SkillEvolutionAgent`。`SkillEvolutionAgent` 已是无默认工具的基础设施 Agent，只提交草稿。

## Goals / Non-Goals

**Goals:**

- 抽出一次无副作用的模型判定，技能自进化是第一个调用方。
- 确定性短路仍在模型之前。
- 模型失败时行为与今天的关键词门一致。
- 判定原因继续进 `skill_evolution_records.router_gate`。

**Non-Goals:**

- 不改习惯、专业区、时间线，也不把 Markdown 准入分换成这个门。
- 不改草稿门槛：信心 0.72、`atomic`、非 Workflow、相似技能只标记合并。
- 不让对话 Agent 分叉去改已安装的 `SKILL.md`。
- 不在本变更里迁移权限、沙箱、密钥以外的其他业务门。

## Decisions

1. **新模块 `backend/src/memory/model_review.py`，不放进 `admission.py`。**  
   准入同时管历史、Markdown 和技能，把模型审核塞进去会让下一个语义门反向依赖记忆准入。模块只提供 `review(request) -> decision`。测试注入可替换的调用函数，单测不打真模型。  
   备选：继续把分数混进 `apply_model_admission_scores`。那是加权分，否决权仍在关键词抬分上，解决不了「没撞上词就被挡掉」。

2. **判定是放行 / 拒绝，不是打分。**  
   请求带 `purpose`、问题、证据、放行标准和拒绝标准。响应是 `allow`、`reason`、`confidence`、`source`（`model` / `rule` / `fallback`）。技能触发把 `allow` 映射成现有 `SkillEvolutionGateDecision.should_evaluate`，`reason` 和 `source` 放进 `signals` / `reason`，这样队列不用新字段。  
   备选：让 `SkillEvolutionAgent` 自己兼任门。那一次调用已经在写草稿，拒绝时仍付出草稿级 token，而且别的门无法复用。

3. **技能路径忽略准入里的技能关键词分，保留准入的确定性拒绝。**  
   `reflection._skill_evolution_gate_for_item` 的顺序改为：未完成 Run、空内容、敏感、寒暄 → `source=rule` 拒绝；手动请求 → `source=rule` 放行；其余调用模型审核。`should_evaluate_skill` 不再作为技能触发的否决。历史节点和 Markdown 是否写入仍走现有准入分。  
   备选：先跑关键词、只有边界分才叫模型。那样没撞上词的回合仍然进不了模型，和这次要改的行为相反。

4. **时机：成功落库之后、后台、一轮一次。**  
   挂在现有 `finalize_successful_leader_run` → `_schedule_skill_evolution`。助手消息已经写入，回复已经可以返回，审核不进 SSE。`finalize_failed_leader_run` 不调度。不采用 Hermes 的「每 10 个用户回合 / 每 10 次工具调用」计数器：这里每轮成功收尾本来就会进后台，要换掉的是关键词否决，不是再加一道次数门。确定性短路（空、未完成、敏感、寒暄、手动请求）在组上下文和调用模型之前完成。  
   备选：把审核插进主 Agent 回合中。那会拉长用户等待，也和「门只做判定」冲突。

5. **上下文管理学 Hermes：摘要在前，近期原文在后，被吃掉的原文不重放。**  
   Hermes 审的是压缩之后的 `messages`：保护下来的开头、一条压缩摘要、还留着的近期回合，再加审核指令。引力的对应物是 AgentScope 的 `state.summary` 加 `state.context`。`chat_messages` 仍是完整落库记录，审核不从那里把被摘要替换的历史再灌进去。  
   拷贝时机是 `finalize_successful_leader_run` 调度后台任务之前，对当前 Agent 做一次深拷贝。后台任务稍后才跑，不能再读活着的 `state`，否则下一轮会改到这份材料。审核 Agent 是一次性 JSON 调用，不再对这份材料做第二次压缩。  
   渲染规则：摘要原样放上，没有摘要就省略这一段。`state.context` 里每条消息保留角色和正文；工具调用留名称、状态和截断后的结果，沿用现有轨迹上限（最多 12 条，参数 160 字、结果 200 字）。思考块去掉。本轮已完成技能只带 id 和 name。习惯、专业区、时间线、`SKILL.md` 正文不进入。用户句和助手最终回覆若已在近期上下文里，不另抄一份。  
   请求形状：

```text
{{compression_summary}}       无压缩则整段不出现
{{recent_context[0]}}
...
{{recent_context[n-1]}}       压缩后仍留着的消息，含本轮
{{activated_skills}}          仅 id 与 name，可为空

{{hermes_skill_review_criteria}}
只返回 JSON：{"allow": true 或 false, "confidence": 0 到 1, "reason": "中文理由"}
```

   `{{hermes_skill_review_criteria}}` 抄 Hermes 只审技能那一版的判断标准：要积极、类级技能、用户纠正是一等信号、顺畅且没有新手法可以说不写、环境失败和没跑通的尝试不要写成经验。删掉 `skill_view`、`skill_manage`、先读后写、支援档目录和「只能调用技能管理工具」。那些句子是给会改文件的分叉用的；这道门没有工具，放行之后仍由 `SkillEvolutionAgent` 写草稿。  
   备选：只送本轮用户句和最终回覆。压缩后的前因会丢，和 Hermes 不一致。另一备选是整段粘贴 Hermes 原文，模型会去调用不存在的 `skill_manage`。

6. **证据沿用已经脱敏的工具链摘要。**  
   执行体复用 `extraction_agents._run_json_agent`：`include_default_tools=False`、无额外工具、低温度、最多 2 步 ReAct。  
   备选：像 Hermes 那样分叉本轮对话 Agent。本仓库的草稿生成已经是专用基础设施 Agent，分叉会把审核和执行混在一次有工具的回合里。

7. **失败回退调用现有 `evaluate_skill_evolution_router_gate`。**  
   超时、异常、非 JSON 都算失败。回退结果的 `source` 标为 `fallback`。回退函数内部已有的敏感和未完成检查再跑一次无害。模型成功时不再调用该函数。

## Risks / Trade-offs

- [每轮成功对话多一次小模型调用] → 只发生在确定性短路之后；寒暄和敏感内容不调用。草稿生成仍只在放行后发生。
- [模型把闲聊放行，草稿变多] → 草稿门槛和相似合并不变；拒绝理由留在记录里，便于收紧提示词。
- [回退路径让关键词门还留在代码里] → 这是故障行为，不是主路径。主路径测试必须断言模型放行时关键词函数不被当作否决。
- [判定抖动] → 温度放低，并记录理由；不把单次判定写成跨轮状态。

## Migration Plan

无数据迁移。已有 `router_gate` JSON 多一个来源标记，旧记录没有该标记时队列照常显示 `reason`。回滚时恢复「准入技能分 + 关键词 router gate」，记录格式兼容。

## Open Questions

无。其他语义门何时接入另开变更。
