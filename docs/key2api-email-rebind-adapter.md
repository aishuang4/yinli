# key2api 适配：同步按邮箱认人

> 给 key2api。引力侧草案是 `docs/key2api-email-rebind.md`。请求路径、字段和鉴权都不变，改的是同一邮箱再次同步时的结果。

## 要改的接口

`POST /api/v1/internal/accounts/sync`

鉴权仍是服务令牌：`Authorization: Bearer <sync token>`。

请求体不变：

```json
{
  "external_id": "usr_0123456789abcdef0123456789abcdef",
  "email": "user@example.com",
  "display_name": "user@example.com",
  "status": "active"
}
```

`email` 比较前做 trim 和转小写，与引力 `normalize_email` 一致。`status` 只有 `active` 和 `disabled`。

成功响应不变，HTTP 200：

```json
{
  "user_id": "12",
  "token_id": "34",
  "token": "k2a_...",
  "status": "active"
}
```

`token` 必须是这把仍可调用 `/v1/models` 和 `/v1/chat/completions` 的 Key。`status` 必须等于本次请求里的 `status`。

用量接口不改路径：`GET /api/v1/internal/accounts/usage?external_id=`。改绑之后，它必须按新的 `external_id` 查到同一个账号，返回原来的 `token_quota`、`used`、`remaining`。

## 查找和结果

只按邮箱认账号。`external_id` 是引力这次的用户 ID，可以变。

1. 邮箱已有账号，且名为 `yinli` 的 `k2a_` 还在。
   - 把该账号的 `external_id` 改成请求里的值。旧 `external_id` 不再指向这个账号。
   - 把账号和这把 Key 的状态设成请求里的 `status`。`disabled` 只是停用，不删除账号、不换 Key、不清额度。之后再同步 `active` 时启用同一把 Key。
   - 返回原来的 `token`、原来的 `user_id` 和 `token_id`。不轮换 Key，不重置 `used`。
2. 邮箱已有账号，但这把 Key 已经不在。
   - 同样改绑 `external_id`，状态按请求设置。
   - 新建一把名为 `yinli` 的 `k2a_`，额度按新账号的默认额度。
   - 返回这把新 Key。
3. 邮箱没有账号。
   - 按现在的新建逻辑创建账号和 `k2a_`，`external_id` 用请求里的值。
   - 返回新 Key。

同一个邮箱加同一个 `external_id` 重复同步，必须返回同一把 Key，不能再开一把。

## 不再使用的冲突

同一邮箱已经绑在另一个 `external_id` 上时，不要再回 409，也不要拒绝交回旧 Key。这就是改绑。

409 只留给请求不合法，或服务令牌无权操作这个账号。错误正文不要带 Key。

## 验收

准备一个已有额度消耗的邮箱 `a@example.com`，Key 为 `k2a_old`，`external_id` 为 `usr_old`。

1. 用 `usr_new`、`a@example.com`、`status=active` 同步。响应 200，`token` 为 `k2a_old`。`GET .../usage?external_id=usr_new` 的 `used` 与改绑前相同。`usr_old` 查不到这个账号。
2. 再同步一次，`status=disabled`。`k2a_old` 不能再调模型。账号和 `used` 仍在。
3. 再同步 `status=active`。仍是 `k2a_old`，模型调用恢复。
4. 在 key2api 删除该账号后，用 `usr_another` 和同一邮箱同步。响应是一把新的 `k2a_`，额度是新账号默认值。
