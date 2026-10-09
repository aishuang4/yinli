# Proposal

## Why

技能自进化的触发门用关键词和打分决定「值不值得叫模型」。一句真正可复用的纠正如果没撞上「技能、流程、复用」这类词，就不会进入 `SkillEvolutionAgent`。准入模块自己也写了首版规则以后可以换成模型 gate。现在要把这层语义判断收成可复用的模型审核，先换掉技能自进化的触发门。

## What Changes

- 新增模型审核门：一次小判定，只回答放行或拒绝，并给出理由和信心。它不写技能、不改记忆、不执行工具。
- 技能自进化的自动触发改为：成功回合落库后的后台任务里，确定性短路之后，由模型审核决定是否进入现有 `SkillEvolutionAgent`。关键词列表不再单独否决。
- 审核提示词抄 Hermes 技能审的判断标准（类级技能、用户纠正、不要把失败写成经验）。删掉其中的 `skill_manage` / `skill_view` 指令，结尾改成只返回放行 JSON。
- 审核上下文向 Hermes 看齐：压缩后注入「会话摘要 + 还留在上下文里的近期原文」，不重放被摘要吃掉的原文，也不只看本轮最后一问一答。摘要和近期原文在成功收尾时从当前 Agent 状态拷走。
- 模型不可用或输出无法解析时，回退到现行关键词规则，并记下回退原因。
- 判定写入现有技能自进化记录的 `router_gate`，供队列和审计查看。
- 空回合、未完成 Run、寒暄、疑似敏感内容仍由规则直接拒绝，不调用模型。
- 多层记忆（习惯、专业区、时间线）的写入路径不变。手动「把上一轮做成技能」仍直接放行。
- 本变更不迁移其他业务门。契约先做成可复用，后续语义门再逐个接入。

## Capabilities

### New Capabilities

- `model-review-gate`：可复用的语义审核门，以及它作为技能自进化自动触发的第一处接入。

### Modified Capabilities

- （无。`skills` 主 spec 只管目录、启用和「所有 Agent 可用」，不管自进化何时触发。注册后的技能如何进入目录不变。）

## Impact

- 后端：`backend/src/memory/admission.py`、`backend/src/memory/skill_evolution.py`、`backend/src/memory/reflection.py`、`backend/src/memory/extraction_agents.py`。成功收尾仍从 `completion.py` 后台触发，不进 Chat SSE。
- 存储：沿用 `skill_evolution_records.router_gate`，不新表。
- 测试：补模型放行、模型拒绝、模型失败回退、确定性短路不调用模型。
- 不改：习惯 / 专业区 / 时间线、`SkillEvolutionAgent` 的草稿门槛（0.72、原子化、相似合并）、技能目录开关、Chat SSE。
