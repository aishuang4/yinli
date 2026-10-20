---
name: yinli-catalog
description: 通过约定目录里的文件了解和修改本系统的持久 Agent、Skill、MCP、HTTP 工具和定时任务定义。用户要查看系统有哪些 Agent、修改提示词、新建助手或登记定时任务时使用。
---

# YINLI Catalog

本系统的持久 Agent 定义是文件，不是数据库。不要调用任何「系统 CRUD 工具」，也不要改 `models.json` 或密钥目录。

## 了解系统

读取当前项目下的：

```text
.agents/INDEX.md
```

里面有 workspace、catalog_root、系统 Agent、持久 Agent 的 path，以及解析错误。

## 修改 Skill

通过 Skills 页面注册、导入、查看或删除 Skill。Skill 包统一保存在应用数据目录：

```text
<USER_DATA_ROOT>/skills/<name>/SKILL.md
```

不要在项目 `.agents/skills`、Workspace catalog 或 HOME 兼容目录另建 Skill；这些目录不参与加载。不要把系统包 `yinli-catalog`、`yinli-charts`、`word-documents` 覆盖成用户 Skill。

MCP / HTTP 定义在 INDEX 的 `catalog_root` 下：

```text
mcp/<id>.yaml
tools/<id>.yaml
```

可以改 URL、描述和参数，但不要写入 Token。密钥用 `auth_ref: secret:...` 或 `header_refs`。

## 定时任务

Chat 对话里优先用 `ScheduleJob` / `ConfirmJob` 登记。也可以在项目下新建：

```text
.agents/jobs/<id>.md
```

frontmatter 需要 `name`、`agent_id`、`schedule_kind`、`schedule_expr`、`timezone`；正文是每次到期要执行的提示词。`desired_status: active` 才会进入调度，`paused` 只保存定义。Job Agent 必须是 `role=job` 的执行者（例如 `system-websearch-job`）。

## 修改 Agent

编辑 INDEX「持久 Agent」表中的 `path`（绝对路径）。文件格式：

```markdown
---
id: researcher
name: 研究助手
role: chat
description: 检索与综述
skills: []
mcp: []
knowledge_tools: []
max_react_iters: 40
---

系统提示词写在这里。
```

- `id` 必须和文件名（不含 `.md`）一致。
- `role` 只能是 `chat` 或 `job`。
- 不要把 Chat Agent 写到 `.agents/agents/`，那是 Subagent。
- 系统 Agent（`default`、`system-websearch-job`）身份不能改、不能删；要改提示词请按 INDEX 里的 overlay_path 创建覆盖文件。

## 新建 Agent

在 INDEX 的 `catalog_root/profiles/` 下新建 `<id>.md`。下一轮对话会自动扫描，无需特殊工具。

## 不要做

- 不要修改 `models.json`、密钥目录、本 INDEX（下次运行会覆盖）。
- Docker 沙箱里 catalog 只是只读投影，改定义请用本地执行或 Agents 页面。
