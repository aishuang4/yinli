# Dynamic Workflow

> 状态：2026-10-09。未上线。不要按本文改 Chat。现网的任务流仍是独立实验页，由产品档 `surfaces.workflow` 控制。

目标不是固定四个节点的动画。若以后做，它是独立工作区：模型生成可审查的脚本，运行时按脚本编排 Agent，并把阶段、变量和产物落库。不进入 `POST /api/chat/send`，也不成为 Chat 的第三种 mode。LangGraph / LangChain 只可作编排候选，SQLite 仍是产品事实源。
