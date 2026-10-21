# 階段 2 進度（2026-10-21）

分支 `refactor/context-storage`。階段 1 已提交 `a35233f`。階段 2 仍未提交。不要提交，除非 Barbara 明確要求。不要做階段 3–5，不要改 `frontend/`。

測試命令：

```bash
cd backend && UV_PROJECT_ENVIRONMENT=/Users/aishuang/agent-workstation/backend/.venv uv run pytest test -q --tb=line --ignore=test/test_team_permissions.py
```

工作樹沒有自己的 `.venv`。最近一次全量：**1197 passed，2 skipped**。`test_team_permissions.py` 缺 `team_runtime/execution_budget.py`，階段 1 提交裡就不存在，這次沒動，全量用 `--ignore` 跳過。

## 已落地

`backend/src` 裡 `workspace_id` / `workspaceId` 為零。

- Schema：刪 `workspaces`、`project_resource_bindings`、`project_job_recipients`、`skills` 表；專案身份是 `projects.slug` + `project_folders`。
- 啟動封存：舊 `agent_workstation.db`、`workspaces/`、或 `state.db` 含 `workspaces` 表時移到 `backups/legacy-<ts>/`。
- 路徑：`agents_root()`、`project_default_root(slug)`、`session_root()`。Job 定義只掃 `agents/jobs`。MCP / HTTP 只掃租戶 `mcp/`、`tools/`。
- 預設 Project 若主目錄仍指向 PyInstaller `_MEI` 或已不存在的 `agent-workstation` 臨時路徑，啟動時改寫到 `project_default_root("default")`。
- `RuntimeContext` 身份欄位是 `project_slug`。檔案範圍是授權目錄。
- Job 檔同步有重入保護：`create_job` 裡的 `list_jobs` 不會再套進同一次同步。讀取 Job 時用定義檔蓋過 sqlite 快取欄。
- 通知已讀事件帶 `projectSlug`，訂閱過濾才收得到。
- 全新租戶預設 Project 可以發一則訊息：`test_project_phase2.py::test_default_project_can_send_a_message`。執行期被換成固定回覆，不斷言真實模型。

## 不要做

階段 3（能力裁決、Job Skill 凍結）、階段 4 前端、階段 5 清理。不要 commit。
