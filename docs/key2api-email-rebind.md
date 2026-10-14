# 引力侧：按邮箱取回平台 Key

> 状态：引力侧校验和测试在 `feat/key2api-email-rebind`。key2api 未按 `docs/key2api-email-rebind-adapter.md` 改完之前，同一邮箱绑在另一个 `external_id` 上仍会 409，本机不会写下 Key。现行说明仍以 `docs/architecture-overview.md` 为准。

## 决定

计费身份是邮箱，不是引力用户 ID。引力账号注销、管理员删除、或直接改库把用户清掉，都不删除 key2api 上的账号、Key 和已用额度。

用户用同一个邮箱再次通过验证并注册后，同步仍走现有的 `POST /api/v1/internal/accounts/sync`。key2api 若还留着这个邮箱，就返回原来的 `k2a_`；若那边账号已经不在，就返回新建的 `k2a_`。引力把返回的 Key 写进当前用户的 `auth_provider_keys`。Key 不下发到浏览器、日志和模型目录。

## 引力要改的内容

运行时协议不增字段，不新增接口。`sync_provider_status` 已经会把响应里的 `k2a_` 存到当前 `user_id`。要补的是契约落地后的校验、测试和文档。

1. `backend/src/server/auth/key2api.py`
   - 请求体保持 `external_id`、`email`、`display_name`、`status`。
   - 响应仍要求 `token` 以 `k2a_` 开头，并带 `user_id`、`token_id`。
   - 响应里的 `status` 必须和本次请求的 `status` 一致。不一致则视为同步失败，不写入 `auth_provider_keys`，沿用现在的日志，下次登录再试。
2. 删除路径保持 `tenancy/lifecycle.py` 的 `delete_tenant`：只同步 `disabled`，然后删本机租户目录和账号。不调用 key2api 删除。远程停用失败仍不回滚本机删除。用户下次用同一邮箱注册时，用 `status=active` 再同步一次，把原 Key 启用并改绑到新 ID。
3. 直接改库清掉用户时，没有本机残留可修。恢复只发生在下一次验证通过后的 `ensure_provider_key`。
4. 测试改 `backend/test/test_key2api_sync.py`：
   - 新 `usr_…` 同步时，假 key2api 按邮箱返回一把已有的 `k2a_`。断言本机 Key 就是这把，用量查询用的是新 `external_id`。
   - 假 key2api 表示邮箱已不存在并返回新 `k2a_`。断言本机写下这把新 Key。
   - 响应 `status` 与请求不一致时，不写 `auth_provider_keys`。
   - 接口响应、日志字符串里仍然没有 `k2a_`。
5. 行为上线后，改这三处的现行说明，删掉「409 不交回旧 Key」：`AGENTS.md`、`docs/architecture-overview.md`、`docs/project-progress-handoff.md`。然后删除本草案和适配草案。

## 不做

- 不做「给邮箱就返回 Key」的公开接口。
- 不在删除用户时清理 key2api。
- 不把旧的引力用户 ID 写回新账号。新注册仍然是新的 `usr_…`，只是平台 Key 和额度还是原来的。
