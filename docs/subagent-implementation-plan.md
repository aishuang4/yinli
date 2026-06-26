# SubAgent 实现方案：Agent Workstation (Python + AgentScope 2.0)

> 状态：历史方案。旧 Subagent Runtime 已于 2026-06-26 下线，本文不再代表当前实现。后续重启 Subagent 设计时必须以新的 Chat 事件契约和运行时生命周期为准。
>
> 基于 Agent Workstation 现有代码扩展，对齐掘金文章的 SubAgent 设计原则
> 文章：《SubAgent 原理深度解析》 https://juejin.cn/post/7632162180318920745
>
> 项目路径：/Users/aishuang/agent-workstation/
> 现有关键文件：
> - backend/src/agents/subagent_tool.py — CallAgentTool（已有）
> - backend/src/agents/runtime_types.py — SubagentRuntimeSpec / SubagentRuntimeContext（已有）
> - backend/src/agents/factory.py — Agent 工厂 + toolkit 注册（已有）

---

## 一、现有 SubAgent 能力盘点

现有 `CallAgentTool` 已经实现了 **Agent as Tool 范式**的核心能力：

```
CallAgentTool(ToolBase)
├── 注册为工具，在主 Agent 的 Toolkit 中可用
├── 隔离上下文：每次调用通过 factory 创建独立 Agent 实例
├── 深度控制：max_depth（默认 2），call_stack 防止循环
├── 权限继承：restrict_child_guardrails() 按父 Agent 限制子 Agent
├── 输出检测：_is_incomplete_subagent_output() 检测"准备性话语"并自动重试
├── 输出裁剪：trim_tool_text() 防止超大回复污染主上下文
├── 事件推送：SUBAGENT_CALL_START / END / FAILED → SSE 前端
├── 多轮能力：child_agent.reply_stream() 流式输出
└── 环检测：self.spec.id in self.context.call_stack
```

**与文章原则的差距：**

| 文章原则 | 现有 | 差距 | 优先级 |
|----------|------|------|--------|
| Markdown 定义文件 | ❌ SubAgent 代码配置 | 无文件定义 → 无法复用/共享 | P1 |
| Description 驱动自动调度 | ❌ 手动注册到 Toolkit | 没有自动匹配 | P1 |
| 顺序执行 (Sequential) | ❌ 只有单次调用 | 无 Session State 传递 | P1 |
| 并行执行 (Parallel) | ❌ 只有单次调用 | 无 Flux.merge 并发 | P2 |
| 循环模式 (Loop / Eval-Opt) | ❌ 只有单次调用 | 无多次迭代 | P2 |
| Handoff 控制权移交 | ❌ 只有 Tool 范式 | 无控制权移交 | P3 |
| per-SubAgent 模型配置 | ⚠️ SubagentRuntimeSpec 有 model_name | 已间接支持，需暴露 | P1 |
| 成本追踪 | ❌ | 无 per-subagent 用量统计 | P3 |

---

## 二、新增/修改文件清单

### 需要新建

```
backend/src/agents/
├── subagent_definition.py    [新] SubAgent 定义文件解析器 + 注册表
├── subagent_orchestrator.py  [新] 三种执行模式编排器
├── subagent_state.py         [新] Session State 共享状态
└── subagent_handoff.py       [新] Handoff 控制权移交

backend/src/server/routes/chat/
└── subagent_dispatcher.py    [新] 自动调度中间件（Description 匹配）
```

### 需要修改

```
backend/src/agents/subagent_tool.py      [改] 增加 Session State 注入
backend/src/agents/runtime_types.py      [改] SubagentRuntimeSpec 增加定义文件引用
backend/src/agents/factory.py            [改] 支持从定义文件创建 SubAgent
backend/src/server/routes/chat/intent_router.py   [改] SubAgent 自动调度
backend/pyproject.toml                   [改] 依赖（yaml 库用于 frontmatter）
```

### 目录约定

```
~/.agent-workstation/agents/         # 用户级 SubAgent 定义
    └── security-auditor.md

<project-root>/.agent-workstation/agents/   # 项目级
    └── project-spec.md

backend/src/agents/builtin/          # 内置 SubAgent
    ├── researcher.md
    └── code-reviewer.md
```

---

## 三、SubAgent 定义文件格式

### 3.1 文件样例

```markdown
---
name: security-auditor
description: >
  安全审计专家。当需要检查代码漏洞、安全配置、
  OWASP Top 10 风险时触发。
display_name: "安全审计 🔒"
model_name: qwen-max
temperature: 0.3
max_react_iters: 20
timeout_seconds: 120
communication: tool      # tool | handoff
execution_mode: single    # single | sequential | parallel | loop
color: "#FF4444"
tags: [security, audit, code-review]
allowed_tools: [Read, Grep, Glob]
disallowed_tools: [Write, Edit, Bash]
---

你是一个专业的代码安全审计员。

### 审查重点
- SQL 注入、XSS、CSRF 等 OWASP Top 10 漏洞
- 硬编码密钥和凭证泄露
- 不安全的依赖项和过期的第三方库

### 输出格式
按严重程度分组输出：
- ❌ **Critical**: 必须立即修复
- ⚠️ **Warning**: 建议修复
- ℹ️ **Info**: 仅供参考
```

### 3.2 数据模型: SubagentDefinition

```python
# backend/src/agents/subagent_definition.py

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import yaml
import re


class CommunicationMode(str, Enum):
    TOOL = "tool"        # Agent as Tool 范式
    HANDOFF = "handoff"  # 控制权移交范式


class ExecutionMode(str, Enum):
    SINGLE = "single"
    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"
    LOOP = "loop"


class Scope(str, Enum):
    BUILTIN = "builtin"
    USER = "user"
    PROJECT = "project"
    CLI = "cli"


@dataclass
class SubagentDefinition:
    name: str
    description: str
    system_prompt: str
    display_name: str = ""
    model_name: str = ""  # 空字符串 = 继承父 Agent
    temperature: float | None = None
    max_react_iters: int = 20
    timeout_seconds: int = 120
    communication: CommunicationMode = CommunicationMode.TOOL
    execution_mode: ExecutionMode = ExecutionMode.SINGLE
    color: str = "#4A90D9"
    tags: list[str] = field(default_factory=list)
    allowed_tools: list[str] = field(default_factory=list)
    disallowed_tools: list[str] = field(default_factory=list)
    scope: Scope = Scope.USER
    source_path: str = ""

    # 执行模式专用字段
    steps: list["StepConfig"] = field(default_factory=list)  # sequential/parallel
    generator_name: str = ""         # loop: 生成器
    evaluator_name: str = ""         # loop: 评估器
    max_iterations: int = 5          # loop: 最大迭代
    termination_threshold: float = 0.9  # loop: 终止阈值

    @classmethod
    def parse(cls, markdown: str, scope: Scope = Scope.USER,
              source_path: str = "") -> "SubagentDefinition":
        """从 Markdown 文件中解析 YAML frontmatter + 正文。"""
        # 分离 frontmatter 和正文
        match = re.match(r'^---\s*\n(.*?)\n---\s*\n(.*)$', markdown, re.DOTALL)
        if not match:
            raise ValueError("Missing YAML frontmatter (--- ... ---)")

        frontmatter = yaml.safe_load(match.group(1))
        body = match.group(2).strip()

        return cls(
            name=frontmatter.get("name", ""),
            description=frontmatter.get("description", ""),
            system_prompt=body,
            display_name=frontmatter.get("display_name", ""),
            model_name=frontmatter.get("model_name", ""),
            temperature=frontmatter.get("temperature"),
            max_react_iters=frontmatter.get("max_react_iters", 20),
            timeout_seconds=frontmatter.get("timeout_seconds", 120),
            communication=frontmatter.get("communication", "tool"),
            execution_mode=frontmatter.get("execution_mode", "single"),
            color=frontmatter.get("color", "#4A90D9"),
            tags=frontmatter.get("tags", []),
            allowed_tools=frontmatter.get("allowed_tools", []),
            disallowed_tools=frontmatter.get("disallowed_tools", []),
            scope=scope,
            source_path=source_path,
        )
```

### 3.3 注册表: SubagentRegistry

实现多级作用域扫描，低优先级同名被高优先级覆盖：

```python
# backend/src/agents/subagent_definition.py (续)

import os
from pathlib import Path


class SubagentRegistry:
    """SubAgent 注册表：按作用域优先级扫描目录。"""

    def __init__(self):
        self._definitions: dict[str, SubagentDefinition] = {}

    def discover(self) -> dict[str, SubagentDefinition]:
        """按优先级扫描（低→高），同名覆盖。"""
        self._definitions = {}

        # 1. 内置 (最低优先级)
        builtin_dir = Path(__file__).parent / "builtin"
        self._load_dir(builtin_dir, Scope.BUILTIN)

        # 2. 用户级 ~/.agent-workstation/agents/
        user_dir = Path.home() / ".agent-workstation" / "agents"
        self._load_dir(user_dir, Scope.USER)

        # 3. 项目级 (CWD 下)
        project_dir = Path.cwd() / ".agent-workstation" / "agents"
        self._load_dir(project_dir, Scope.PROJECT)

        return dict(self._definitions)

    def _load_dir(self, directory: Path, scope: Scope):
        if not directory.exists():
            return
        for fpath in sorted(directory.glob("*.md")):
            content = fpath.read_text(encoding="utf-8")
            try:
                definition = SubagentDefinition.parse(content, scope, str(fpath))
                # 同名覆盖（高优先级在后）
                self._definitions[definition.name] = definition
            except ValueError as e:
                logger.warning("Skipping %s: %s", fpath, e)

    def get(self, name: str) -> SubagentDefinition | None:
        return self._definitions.get(name)

    def get_all(self) -> list[SubagentDefinition]:
        return list(self._definitions.values())

    def to_runtime_specs(
        self,
        parent_model: str,
        parent_guardrails: AgentGuardrails | None,
    ) -> list[SubagentRuntimeSpec]:
        """将注册表中的定义转为 SubagentRuntimeSpec（供 factory 使用）。"""
        specs = []
        for defn in self.get_all():
            specs.append(SubagentRuntimeSpec(
                id=defn.name,
                name=defn.display_name or defn.name,
                description=defn.description,
                system_prompt=defn.system_prompt,
                model_name=defn.model_name or parent_model,
                guardrails=build_guardrails_from_definition(
                    defn, parent_guardrails
                ),
                max_depth=2,
            ))
        return specs
```

---

## 四、三种执行模式

### 4.1 执行模式编排器

```python
# backend/src/agents/subagent_orchestrator.py

from dataclasses import dataclass
from typing import Any


@dataclass
class StepConfig:
    """执行流水线中的单个步骤。"""
    subagent_name: str
    task_template: str      # 支持 ${state.key} 变量替换
    output_key: str         # 输出存入 Session State 的 key
    is_aggregator: bool = False  # 是否为汇总步骤


class SessionState:
    """SubAgent 链间的共享状态。"""
    def __init__(self):
        self._data: dict[str, Any] = {}

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any):
        self._data[key] = value

    def interpolate(self, template: str) -> str:
        """将模板中的 ${state.key} 替换为实际值。"""
        import re
        def _replace(match):
            key = match.group(1)
            return str(self._data.get(key, match.group(0)))
        return re.sub(r'\$\{state\.(\w+)\}', _replace, template)
```

### 4.2 Sequential 顺序执行

```python
# backend/src/agents/subagent_orchestrator.py

class SequentialOrchestrator:
    """顺序执行模式：前一个输出作为后一个输入。"""

    def __init__(self, factory_func):
        # factory_func(spec, task, context) → output string
        self._factory = factory_func

    async def execute(
        self,
        steps: list[StepConfig],
        definitions: dict[str, SubagentDefinition],
    ) -> str:
        state = SessionState()

        for step in steps:
            defn = definitions.get(step.subagent_name)
            if not defn:
                raise ValueError(f"SubAgent '{step.subagent_name}' not found")

            # 变量替换：将 ${state.key} 替换为前几步的输出
            task = state.interpolate(step.task_template)

            # 调用 SubAgent
            output = await self._factory(defn, task, state)

            # 写入 Session State
            state.set(step.output_key, output)

        return state.get(steps[-1].output_key, "")
```

### 4.3 Parallel 并行执行

```python
# backend/src/agents/subagent_orchestrator.py

import asyncio

class ParallelOrchestrator:
    """并行执行模式：多个 SubAgent 同时执行，最后汇总。"""

    def __init__(self, factory_func):
        self._factory = factory_func

    async def execute(
        self,
        steps: list[StepConfig],
        definitions: dict[str, SubagentDefinition],
    ) -> str:
        # 所有非汇总步骤并行
        work_steps = [s for s in steps if not s.is_aggregator]
        aggregator_steps = [s for s in steps if s.is_aggregator]

        async def run_one(step: StepConfig) -> tuple[str, Any]:
            defn = definitions.get(step.subagent_name)
            output = await self._factory(defn, step.task_template, None)
            return step.output_key, output

        # 并发执行
        results = await asyncio.gather(*[run_one(s) for s in work_steps])

        # 收集结果到 state
        state = SessionState()
        for key, value in results:
            state.set(key, value)

        # 如果有汇总步骤，调用汇总 SubAgent
        if aggregator_steps:
            agg = aggregator_steps[0]
            defn = definitions.get(agg.subagent_name)
            task = state.interpolate(agg.task_template)
            return await self._factory(defn, task, state)

        return str({k: v for k, v in results})
```

### 4.4 Loop 循环执行（Evaluator-Optimizer）

```python
# backend/src/agents/subagent_orchestrator.py

class LoopOrchestrator:
    """循环执行模式：生成 → 评估 → 终止条件。"""

    def __init__(self, factory_func):
        self._factory = factory_func

    async def execute(
        self,
        defn: SubagentDefinition,
        task: str,
    ) -> str:
        generator_def = defn.generator_name
        evaluator_def = defn.evaluator_name
        max_iter = defn.max_iterations
        threshold = defn.termination_threshold

        current_output = ""
        for i in range(max_iter):
            # 生成
            gen_task = f"第 {i+1}/{max_iter} 轮生成。任务: {task}"
            if current_output:
                gen_task += f"\n\n上次输出:\n{current_output}"
            current_output = await self._factory(
                generator_def, gen_task, None
            )

            # 评估
            eval_task = f"评估以下输出质量（0-1分），>= {threshold} 为通过:\n\n{current_output}"
            eval_result = await self._factory(
                evaluator_def, eval_task, None
            )

            # 解析评分（假设评估器输出 "[SCORE: 0.95]" 格式）
            import re
            match = re.search(r'\[SCORE:\s*([\d.]+)\]', eval_result)
            if match and float(match.group(1)) >= threshold:
                return current_output

        return current_output  # 最大迭代后返回
```

### 4.5 统一编排入口

```python
# backend/src/agents/subagent_orchestrator.py

class SubagentOrchestrator:
    """根据定义中的 execution_mode 自动选择策略。"""

    def __init__(self, factory_func):
        self._factory = factory_func
        self._strategies = {
            ExecutionMode.SEQUENTIAL: SequentialOrchestrator(factory_func),
            ExecutionMode.PARALLEL: ParallelOrchestrator(factory_func),
            ExecutionMode.LOOP: LoopOrchestrator(factory_func),
        }

    async def execute(
        self,
        defn: SubagentDefinition,
        task: str,
    ) -> str:
        if defn.execution_mode == ExecutionMode.SINGLE:
            # 默认单次调用，走现有的 CallAgentTool 逻辑
            return await self._factory(defn, task, None)

        strategy = self._strategies.get(defn.execution_mode)
        if not strategy:
            raise ValueError(f"Unknown execution mode: {defn.execution_mode}")

        if defn.execution_mode == ExecutionMode.LOOP:
            return await strategy.execute(defn, task)

        return await strategy.execute(defn.steps, self._registry)
```

---

## 五、Handoff 控制权移交

```python
# backend/src/agents/subagent_handoff.py

class HandoffSession:
    """一次 Handoff 操作的记录。"""

    def __init__(
        self,
        parent_session_id: str,
        sub_session_id: str,
        subagent_name: str,
        parent_state_snapshot: dict,
    ):
        self.parent_session_id = parent_session_id
        self.sub_session_id = sub_session_id
        self.subagent_name = subagent_name
        self.parent_state_snapshot = parent_state_snapshot
        self.started_at = time.time()
        self.completed = False
        self.summary = ""


class HandoffManager:
    """
    控制权移交管理器。

    流程:
    1. 父 Agent 判断任务适合 Handoff → save_parent_state()
    2. 创建 SubAgent 会话 → emit SUBAGENT_EXPOSED 事件
    3. 前端收到事件 → 显示新对话 Tab
    4. 用户与 SubAgent 直接对话
    5. SubAgent 完成 → restore_parent() + emit 摘要
    """

    def __init__(self, db, event_emitter):
        self._db = db                     # SQLite 存储
        self._emitter = event_emitter     # SSE 事件推送
        self._active: dict[str, HandoffSession] = {}

    async def handoff_to(
        self,
        parent_session_id: str,
        definition: SubagentDefinition,
        parent_state: dict,
    ) -> HandoffSession:
        """父 Agent 移交控制权给 SubAgent。"""

        # 1. 保存父 Agent 上下文快照
        self._save_parent_snapshot(parent_session_id, parent_state)

        # 2. 创建新的 SubAgent 会话
        sub_session_id = f"sub:{definition.name}:{uuid.uuid4().hex[:8]}"
        session = HandoffSession(
            parent_session_id=parent_session_id,
            sub_session_id=sub_session_id,
            subagent_name=definition.name,
            parent_state_snapshot=parent_state,
        )
        self._active[sub_session_id] = session

        # 3. 创建 SubAgent 实例（复用 factory）
        child_agent = await create_subagent_instance(
            definition, parent_session_id
        )

        # 4. 持久化会话记录
        self._db.execute(
            "INSERT INTO chat_sessions (id, title, agent_id, parent_session_id) "
            "VALUES (?, ?, ?, ?)",
            [sub_session_id, f"🔒 {definition.display_name or definition.name}",
             definition.name, parent_session_id],
        )

        # 5. 通知前端：出现新的对话入口
        self._emitter({
            "eventType": "SUBAGENT_EXPOSED",
            "subagentId": definition.name,
            "subagentSessionId": sub_session_id,
            "parentSessionId": parent_session_id,
            "label": definition.display_name or definition.name,
            "color": definition.color,
        })

        return session

    async def restore_parent(self, sub_session_id: str) -> str:
        """SubAgent 完成后，恢复父 Agent。"""
        session = self._active.pop(sub_session_id)
        if not session:
            return ""

        session.completed = True

        # 恢复父上下文
        self._restore_parent_snapshot(
            session.parent_session_id,
            session.parent_state_snapshot,
        )

        # 通知前端 Handoff 结束
        self._emitter({
            "eventType": "SUBAGENT_HANDOFF_RESTORED",
            "parentSessionId": session.parent_session_id,
            "subagentName": session.subagent_name,
            "summary": session.summary,
        })

        return session.parent_session_id

    def _save_parent_snapshot(self, session_id: str, state: dict):
        """持久化父 Agent 状态到 SQLite。"""
        self._db.execute(
            "INSERT OR REPLACE INTO json_store (namespace, key, value) "
            "VALUES (?, ?, ?)",
            ["handoff_snapshots", session_id, json.dumps(state)],
        )

    def _restore_parent_snapshot(self, session_id: str, state: dict):
        """恢复到 SQLite（后续父 Agent 重新加载）。"""
        pass  # 由 chat 路由在下次请求时从 json_store 中读取
```

**前端交互流：**

```
用户: "帮我设计这个系统的技术方案"
  ↓
Chat 路由 → Intent Router 判断 → 匹配 architect SubAgent (communication: handoff)
  ↓
HandoffManager.handoff_to()
  → 保存父 Agent 上下文快照
  → 创建 SubAgent 新会话
  → emit SUBAGENT_EXPOSED 事件
  ↓
前端收到 SUBAGENT_EXPOSED
  → 在聊天列表新增 Tab: "🏗 架构设计"
  → 切换到新 Tab，用户直接与 architect 对话
  ↓
用户与 SubAgent 多轮对话完成
  → SubAgent emit 完成信号
  ↓
HandoffManager.restore_parent()
  → emit SUBAGENT_HANDOFF_RESTORED
  → 前端显示"架构设计已完成"气泡
  → 切回父 Agent Tab
```

---

## 六、自动调度 (Description 匹配)

```python
# backend/src/server/routes/chat/subagent_dispatcher.py

class SubagentDispatcher:
    """
    在 Intent Router 阶段决策：当前用户请求是否需要调度 SubAgent。

    匹配策略（规则优先，LLM 兜底）:
    1. 用户明确 @subagent_name → 直接调度
    2. 用户请求中包含 SubAgent tags 关键词（正则匹配）→ 候选
    3. 多个候选时交给小模型判断哪个最匹配 description
    4. 低于阈值或无匹配 → 不调度，由主 Agent 自己处理
    """

    def __init__(self, registry: SubagentRegistry):
        self._registry = registry

    async def dispatch(
        self,
        user_message: str,
        intent: IntentRoute,
    ) -> list[SubagentRuntimeSpec]:
        """返回本轮应该注入的 SubAgent 列表。"""
        candidates = []

        # 1. 显式 @引用
        for defn in self._registry.get_all():
            if f"@{defn.name}" in user_message:
                candidates.append(defn)

        # 2. 规则匹配（tags + 关键词）
        if not candidates:
            for defn in self._registry.get_all():
                # 简单关键词匹配
                if any(tag in user_message.lower() for tag in defn.tags):
                    candidates.append(defn)

        # 3. 仍然没有候选 → 不调度
        if not candidates:
            return []

        # 4. 转为 RuntimeSpec（供 factory 注入 Toolkit）
        return self._registry.to_runtime_specs(
            parent_model=intent.model_name,
            parent_guardrails=intent.guardrails,
        )
```

**在 Intent Router 中的集成点：**

```python
# backend/src/server/routes/chat/intent_router.py (修改点)

class IntentRouter:
    async def route(self, user_message, session, agent_config):
        # ... 现有路由逻辑 ...

        # 新增：自动调度 SubAgent
        dispatcher = SubagentDispatcher(registry)
        auto_subagents = await dispatcher.dispatch(
            user_message, intent_route
        )

        # 将匹配的 SubAgent 注入 tool_scope
        if auto_subagents:
            intent_route.auto_dispatched_subagents = auto_subagents

        return intent_route
```

---

## 七、修改现有代码

### 7.1 SubagentRuntimeSpec 扩展

```python
# backend/src/agents/runtime_types.py (修改点)

@dataclass(frozen=True)
class SubagentRuntimeSpec:
    """增加定义文件引用字段。"""
    id: str
    name: str
    description: str
    system_prompt: str
    model_name: str
    fallback_names: list[str] = field(default_factory=list)
    model_options: AgentModelOptions = field(default_factory=AgentModelOptions)
    guardrails: AgentGuardrails = field(default_factory=AgentGuardrails)
    mcp_servers: list[McpRuntimeSpec] = field(default_factory=list)
    knowledge_tools: list[KnowledgeToolRuntimeSpec] = field(default_factory=list)
    subagents: list["SubagentRuntimeSpec"] = field(default_factory=list)
    max_depth: int = 2
    memory_enabled: bool = True
    # === 新增 ===
    execution_mode: str = "single"        # single | sequential | parallel | loop
    communication: str = "tool"           # tool | handoff
    color: str = "#4A90D9"                # 前端显示色
    definition_path: str = ""             # 来源文件路径
```

### 7.2 Factory 支持从定义文件创建

```python
# backend/src/agents/factory.py (修改点)

def get_or_create_agent(
    agent_id: str,
    name: str,
    system_prompt: str,
    model_name: str,
    ...,
    # === 新增参数 ===
    subagent_definitions: list[SubagentDefinition] | None = None,
    handoff_manager: HandoffManager | None = None,
    execution_orchestrator: SubagentOrchestrator | None = None,
):
    # ... 现有逻辑 ...

    # 在注册工具时，如果是 SubAgent 模式，注入编排器
    if subagent_definitions:
        for defn in subagent_definitions:
            if defn.execution_mode != "single":
                # 注册编排工具而非直接 CallAgentTool
                toolkit.register(OrchestratedSubagentTool(
                    orchestrator=execution_orchestrator,
                    definition=defn,
                    ...
                ))
```

### 7.3 SubAgent 工具增加 Session State 支持

```python
# backend/src/agents/subagent_tool.py (修改点)

class CallAgentTool(ToolBase):
    def __init__(self, ..., session_state: SessionState | None = None):
        super().__init__(...)
        self.session_state = session_state  # 新增

    async def __call__(self, **kwargs):
        # 调用前注入 Session State 到上下文
        if self.session_state:
            kwargs["_session_state"] = self.session_state

        # ... 现有调用逻辑 ...
```

---

## 八、实现路线图

### Phase 1：定义文件 + 注册表（2天）

- [ ] `subagent_definition.py` — Markdown 解析 + SubagentDefinition 数据类
- [ ] `SubagentRegistry` — 多级作用域扫描 + 同名覆盖
- [ ] 内置示例 SubAgent（researcher.md, code-reviewer.md）
- [ ] `SubagentRuntimeSpec` 扩展字段
- [ ] 测试：解析正确性、作用域优先级、注册表查询

### Phase 2：三种执行模式（3天）

- [ ] `SessionState` — 共享状态
- [ ] `SequentialOrchestrator` — 变量替换 + 链式传递
- [ ] `ParallelOrchestrator` — asyncio.gather + 汇总
- [ ] `LoopOrchestrator` — 评分解析 + 迭代终止
- [ ] `SubagentOrchestrator` — 统一入口
- [ ] 修改 `CallAgentTool` 支持编排模式
- [ ] 测试：三种模式正确执行、状态传递、超时

### Phase 3：自动调度（1天）

- [ ] `SubagentDispatcher` — 关键词匹配 + @引用 + LLM 兜底
- [ ] 集成到 `IntentRouter`
- [ ] 测试：调度准确率、边界情况

### Phase 4：Handoff（2天）

- [ ] `HandoffManager` — 状态快照 + 会话切换 + 恢复
- [ ] SSE 事件 `SUBAGENT_EXPOSED` / `SUBAGENT_HANDOFF_RESTORED`
- [ ] 前端新 Tab 创建 + 切换逻辑
- [ ] 测试：Handoff 完整流程

### Phase 5：前端 UI 增强（2天）

- [ ] SubAgent 调用块渲染（颜色/图标/标签）
- [ ] 并行执行可视化（多个 SubAgent 同时展示）
- [ ] Handoff Tab 切换
- [ ] Token 用量追踪（按 SubAgent 拆分）

---

## 九、与文章原则的对齐

| 文章原则 | 本方案实现 |
|----------|-----------|
| **上下文隔离** | ✅ 已有：CallAgentTool 每次创建独立 Agent |
| **能力约束** | ✅ 已有：guardrails 继承 + 限制，新增定义文件的 allowed_tools/disallowed_tools |
| **Description 驱动** | ✅ 新增：SubagentDispatcher 按 tags/关键词/语义匹配自动调度 |
| **SubAgent 不能再生 SubAgent** | ✅ 已有：max_depth + call_stack 环检测 |
| **顺序模式** | ✅ 新增：SequentialOrchestrator + SessionState |
| **并行模式** | ✅ 新增：ParallelOrchestrator + asyncio.gather |
| **循环模式** | ✅ 新增：LoopOrchestrator (Evaluator-Optimizer) |
| **Agent as Tool** | ✅ 已有：CallAgentTool |
| **Handoff** | ✅ 新增：HandoffManager + SUBAGENT_EXPOSED 事件 |
| **Markdown 定义** | ✅ 新增：YAML frontmatter + 正文，复用 Markdown 格式 |
| **作用域优先级** | ✅ 新增：5 级作用域扫描 |
| **模型路由** | ⚠️ 增强：SubagentDefinition.model_name |
| **maxTurns 防失控** | ✅ 已有：max_react_iters，新增每个定义文件独立配置 |
| **上下文隔离即节省** | ✅ 已有：trim_tool_text 裁剪输出 |
