# Tasks

## 1. 模型审核契约

- [x] 1.1 新增 `backend/src/memory/model_review.py`，定义请求（用途、问题、证据、放行标准、拒绝标准）和判定（放行、理由、信心、来源 `model` / `rule` / `fallback`），并验证调用方可以不依赖 `admission.py` 构造一次判定
- [x] 1.2 用可注入的调用函数实现 `review`，验证单测能在不打真模型的情况下得到放行和拒绝

## 2. 基础设施 Agent

- [x] 2.1 在 `extraction_agents.py` 增加无默认工具、无写工具、低温度、最多 2 步的审核调用，只解析 JSON 判定，并验证异常和非 JSON 向 `review` 返回失败而不是抛到技能注册
- [x] 2.2 把 Hermes 只审技能提示词里的判断标准写成固定上下文：类级技能、纠正信号、不要把失败写成经验；删除 `skill_manage` / `skill_view` / 先读后写。结尾要求只返回 `allow`、`confidence`、`reason`。验证提示词不含技能写入工具名，也不要求命中关键词列表
- [x] 2.3 在成功收尾调度后台任务之前，从当前 Agent 拷贝 `state.summary` 和 `state.context`。审核上下文按摘要、近期消息、已完成技能 id/name、Hermes 判断标准组装。验证已压缩会话看得到摘要和近期原文、看不到被摘要替换的 `chat_messages` 旧文；思考、技能正文、习惯和专业区不在里面。失败收尾不拷贝

## 3. 接上技能触发

- [x] 3.1 调整 `reflection._skill_evolution_gate_for_item`：未完成、空内容、敏感、寒暄走规则拒绝且不调用模型；手动请求走规则放行且不调用模型；其余调用模型审核。验证 `should_evaluate_skill` 的关键词分不再否决技能触发
- [x] 3.2 模型放行时进入现有 `SkillEvolutionAgent`，模型拒绝时不生成草稿，并把理由写入 `router_gate`。验证没有关键词的成功工具链在注入放行时仍会进入草稿生成
- [x] 3.3 模型超时、异常或无法解析时调用 `evaluate_skill_evolution_router_gate`，来源标为 `fallback`。验证回退记录能区分规则回退和模型判定

## 4. 回归

- [x] 4.1 跑技能自进化与准入相关测试，验证草稿门槛（0.72、原子化、相似合并）和习惯 / 专业区路径没有被这次触发门改掉
