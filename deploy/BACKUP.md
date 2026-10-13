# 托管备份

托管数据不从旧的命名卷迁移。换到 `/srv/yinli/data` 之前，先备份再决定保留或清空。

## 控制面

控制面是一个 SQLite 文件，备份时用 SQLite 自己的备份命令，不要直接复制正在写入的文件：

```bash
sqlite3 /srv/yinli/data/control/control.db ".backup /srv/yinli/backups/control.db"
```

里面有账号、会话、租户状态和平台调用审计。审计没有请求或响应正文。

## 租户目录

每个用户一份目录：`/srv/yinli/data/tenants/usr_<32hex>/`。按目录做快照即可，包含该用户的数据库、`models.json`、加密密钥、记忆和 Project 文件。

```bash
tar -C /srv/yinli/data/tenants -czf /srv/yinli/backups/tenants-$(date +%F).tar.gz .
```

密钥文件是 Fernet 密文。没有 `AGENT_WORKSTATION_SECRET_KEY` 就解不开，备份时把主密钥分开保管，不要放进同一份压缩包。

## 不要做的事

不要执行 `docker compose down -v`。命名卷 `yinli-data` 如果还在，那是换路径之前的旧数据；确认备份之后再删。
