# Tasks

## 1. 模型与存储

- [x] 1.1 `Skill` 增加 `availableToAllAgents`（缺省 true）；sqlite `skills.available_to_all_agents` 用 `_ensure_column` 默认 1；`replace_skills` / `_save_user_skills` 往返该字段且 `content` 仍写空；缺列旧库读出来为 true
- [x] 1.2 外部包 `external_skill_overrides` 同时存 `enabled` 与 `availableToAllAgents`；`discover_external_skills` 合并 override；`yinli-catalog` 对外恒为 true

## 2. API

- [x] 2.1 列表 / 详情 JSON 带 `availableToAllAgents`；新增或扩展写入接口（如 `POST /api/skills/{id}/share`）；把 `yinli-catalog` 设为 false 返回 409；未安装 Skill 写共享失败或 409
- [x] 2.2 停用 / 卸载后，即使 override 仍为 true，`get_installed_skill` / Runtime 目录都不再包含该 Skill；补 API 单测

## 3. Runtime 装配

- [x] 3.1 `_merge_file_catalog_skills`（或等价纯函数）对合格 Chat：profile 技能 ∪（已启用且共享或 `ALWAYS_INJECTED`），并经 Project 裁剪；`test_chat_skill_injection_scope.py` 覆盖「两助手未绑定仍看见共享包」与「开关关则必须绑定」
- [x] 3.2 Team Leader 能吃到共享集合；Team Member session、Job firing、群聊 Worker 不因开关增技能；现有 Job / group-studio 测试不回退
- [x] 3.3 共享只扩目录，不把多份 `SKILL.md` 拼进 system prompt；`prompts._build_skill_context_prompt` 仍是按需读取

## 4. 前端与文档

- [x] 4.1 `frontend/src/types/skill.ts` 与 `useSkills` 支持读写共享开关；Skills 详情在启停旁加「所有 Agent 可用」；`yinli-catalog` 开关禁用
- [x] 4.2 Agent 技能选择器标注已共享项；关掉共享后仍勾在 profile 里的技能保持选中
- [x] 4.3 更新 `docs/file-catalog-v1.md` §9.2 / §13.2 与 `AGENTS.md`：办公包可经开关共享，不再写死「必须出现在 Agent 配置」；`frontend` 下 `./node_modules/.bin/tsc -b` 通过

## 5. 回归

- [x] 5.1 `cd backend && uv run pytest test/test_chat_skill_injection_scope.py test/test_catalog_skills.py test/test_skills_package.py test/test_native_skills_runtime.py -q` 全过
