# Agent Workstation

> 基于 AgentScope 2.0 + Electron + Spark Design 的开源桌面 Agent 工作台

类似 Codex 的多 Agent 协作工具，支持 MCP 工具集成、Skills 技能加载、三层记忆系统。

## 快速开始

### 环境要求

- Node.js >= 20
- Python >= 3.11
- uv (Python 包管理)

### 开发模式

```bash
# 1. 安装前端依赖
cd frontend && npm install && cd ..

# 2. 配置后端环境变量
cp backend/.env.example backend/.env
# 编辑 backend/.env，填入 DeepSeek API Key

# 3. 安装后端依赖
cd backend && uv sync && cd ..

# 4. 启动开发服务
./scripts/dev.sh
```

### 访问

- 前端: http://localhost:5173
- 后端 API: http://localhost:8765
- API 文档: http://localhost:8765/docs

## 技术栈

| 层 | 技术 |
|---|---|
| 桌面壳 | Electron |
| 前端 | React 19 + TypeScript + Vite |
| UI 组件 | @agentscope-ai/design + @agentscope-ai/chat |
| 后端 | Python + FastAPI + AgentScope 2.0 |
| 模型 | DeepSeek 默认，可在系统设置切换 OpenAI 兼容供应商 |
| 存储 | SQLite (MVP) + Milvus (预留) |
| 容器 | AgentScope Workspace (Docker) |

## 需求与设计文档

- [Agent Workstation 设计文档](./docs/agent-workstation-design.md)
- [Agent Workstation 前端设计文档](./docs/agent-workstation-frontend-design.md)

## License

Apache 2.0
