# Debian HTTP 临时部署指南 V1

> 状态：部署操作指南，不代表生产安全基线。目标环境为 Debian 单机，公网入口
> `http://103.236.80.228:8000`。本方案没有 TLS 和用户认证，只适合短期、可丢弃的测试环境。

## 1. 部署边界

本方案采用以下拓扑：

```text
Internet :8000
  -> Nginx（React 静态文件 + /api 反向代理）
  -> FastAPI 127.0.0.1:8765
  -> /var/lib/yinli（SQLite、Memory、Skills、Run 和 Artifact）
```

- 公网只开放 TCP `8000` 和现有 SSH TCP `5697`。
- FastAPI 必须继续监听 `127.0.0.1:8765`，不得直接暴露公网。
- 不开放 `5432`；本项目当前使用 SQLite，不依赖 PostgreSQL。
- 不使用 `npm run dev` 或 Vite dev server 提供线上页面。
- 当前应用没有用户认证。访问者可以接触模型、文件和执行工具，因此不要保存真实敏感数据，测试结束后应立即关闭公网端口。

## 2. 发布前准备

### 2.1 确定发布版本

只部署已经提交并推送的 commit 或 tag，不部署本机未提交工作区。先在开发机记录：

```bash
git status --short
git rev-parse HEAD
git remote -v
```

后续命令中的 `<RELEASE_REF>` 必须替换为确认过的 commit SHA 或 tag。

### 2.2 登录服务器

```bash
ssh -p 5697 root@103.236.80.228
```

建议先配置 SSH 公钥，不要在脚本、仓库或聊天记录中保存服务器密码。

## 3. 安装系统依赖

以下命令均在服务器执行：

```bash
apt-get update
apt-get install -y ca-certificates curl git nginx build-essential python3 python3-dev
```

安装 Node.js 24：

```bash
curl -fsSL https://deb.nodesource.com/setup_24.x | bash -
apt-get install -y nodejs
node --version
npm --version
```

安装 `uv`：

```bash
curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
uv --version
```

期望 Node.js 不低于 20，Python 不低于 3.11。

## 4. 创建低权限运行用户和目录

```bash
useradd --system --create-home --home-dir /opt/yinli --shell /usr/sbin/nologin yinli
install -d -o yinli -g yinli -m 0750 /opt/yinli/app
install -d -o yinli -g yinli -m 0750 /var/lib/yinli
install -d -o root -g yinli -m 0750 /etc/yinli
usermod -aG yinli www-data
```

不要用 `root` 运行 FastAPI。LocalWorkspace 的命令执行权限将继承 systemd 服务用户权限。
`www-data` 只通过 `yinli` 组获得读取前端构建产物和穿过应用目录的权限，不能写入应用目录。

## 5. 获取并构建代码

仓库当前远端为 Gitee。若仓库需要认证，请先在服务器配置只读 Deploy Key 或使用受限访问令牌：

```bash
runuser -u yinli -- git clone https://gitee.com/aishuang4/agent-scope.git /opt/yinli/app
cd /opt/yinli/app
runuser -u yinli -- git checkout <RELEASE_REF>
```

安装后端依赖：

```bash
cd /opt/yinli/app/backend
runuser -u yinli -- uv sync --frozen
```

构建前端：

```bash
cd /opt/yinli/app/frontend
runuser -u yinli -- npm ci
runuser -u yinli -- npm run build
test -f /opt/yinli/app/frontend/dist/index.html
```

## 6. 配置运行环境

创建 `/etc/yinli/yinli.env`：

```bash
install -o root -g yinli -m 0640 /dev/null /etc/yinli/yinli.env
nano /etc/yinli/yinli.env
```

填入以下内容，并替换 API Key：

```dotenv
AGENT_WORKSTATION_HOST=127.0.0.1
AGENT_WORKSTATION_PORT=8765
AGENT_WORKSTATION_DATA_DIR=/var/lib/yinli

# Linux 尚无 Keychain/DPAPI 实现。memory 仅用于让运行时读取环境变量中的默认 Key；
# 在设置页面新填的 Key 只存在进程内存，服务重启后会丢失。
AGENT_WORKSTATION_SECRET_STORE=memory

AGENT_WORKSTATION_DEEPSEEK_API_KEY=替换为真实Key
AGENT_WORKSTATION_DEEPSEEK_BASE_URL=https://api.deepseek.com
AGENT_WORKSTATION_DEFAULT_MODEL=deepseek-v4-flash
AGENT_WORKSTATION_FALLBACK_MODELS=["deepseek-v4-pro"]

VECTOR_STORE_PROVIDER=zvec
EMBEDDING_PROVIDER=disabled
```

检查权限，避免普通用户读取密钥：

```bash
stat -c '%U %G %a %n' /etc/yinli/yinli.env
```

期望输出为 `root yinli 640 /etc/yinli/yinli.env`。

## 7. 创建 systemd 后端服务

创建 `/etc/systemd/system/yinli-backend.service`：

```ini
[Unit]
Description=YINLI Agent Workstation Backend
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=yinli
Group=yinli
WorkingDirectory=/opt/yinli/app/backend
EnvironmentFile=/etc/yinli/yinli.env
ExecStart=/opt/yinli/app/backend/.venv/bin/python -m uvicorn src.server.main:app --host 127.0.0.1 --port 8765
Restart=on-failure
RestartSec=3
TimeoutStopSec=30
NoNewPrivileges=true
PrivateTmp=true
UMask=0027

[Install]
WantedBy=multi-user.target
```

启动并验证：

```bash
systemctl daemon-reload
systemctl enable --now yinli-backend
systemctl status yinli-backend --no-pager
curl -fsS http://127.0.0.1:8765/api/health
journalctl -u yinli-backend -n 100 --no-pager
```

若健康检查失败，不要继续开放 Nginx；先检查日志、`.env` 权限和模型配置。

## 8. 配置 Nginx 公网入口

创建 `/etc/nginx/sites-available/yinli`：

```nginx
server {
    listen 8000 default_server;
    listen [::]:8000 default_server;
    server_name _;

    root /opt/yinli/app/frontend/dist;
    index index.html;
    client_max_body_size 100m;

    location /api/ {
        proxy_pass http://127.0.0.1:8765;
        proxy_http_version 1.1;
        proxy_set_header Host $host;

        # Chat 和 Workflow 使用 SSE，必须禁用代理缓冲。
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
        add_header X-Accel-Buffering no;
    }

    location / {
        try_files $uri $uri/ /index.html;
    }
}
```

不要增加 `X-Forwarded-For`：FastAPI 的 `LocalAccessMiddleware` 必须把 Nginx 识别为本机调用方，否则会返回 403。

启用站点：

```bash
rm -f /etc/nginx/sites-enabled/default
ln -s /etc/nginx/sites-available/yinli /etc/nginx/sites-enabled/yinli
nginx -t
systemctl enable --now nginx
systemctl reload nginx
curl -fsS http://127.0.0.1:8000/api/health
```

## 9. 云安全组与防火墙

在云厂商安全组中：

- 保留 TCP `5697`，最好仅允许你的固定公网 IP。
- 临时开放 TCP `8000`。
- 不开放 TCP `8765`、`5432` 或 Docker daemon 端口。

如果服务器使用 UFW：

```bash
ufw allow 5697/tcp
ufw allow 8000/tcp
ufw status verbose
```

不要在未确认 SSH `5697` 已放行前启用或重载防火墙，避免锁死远程连接。

## 10. 验收

服务器内执行：

```bash
curl -fsS http://127.0.0.1:8765/api/health
curl -fsS http://127.0.0.1:8000/api/health
ss -lntp | grep -E ':8000|:8765|:5432'
```

期望结果：

- `8000` 监听公网地址，由 Nginx 占用。
- `8765` 只监听 `127.0.0.1`。
- `5432` 不因本项目新增监听。

浏览器访问 `http://103.236.80.228:8000`，至少验证：

1. 首页和各管理页面可刷新打开。
2. `/api/health` 正常。
3. Chat 可以流式显示文本，而不是等待整轮结束后一次出现。
4. 创建测试会话后刷新页面，历史仍存在。
5. 上传一个无敏感信息的小文件，确认前后端大小限制一致。
6. 重启 `yinli-backend` 后 SQLite、Memory 和会话仍保留。

## 11. 日常操作

查看状态和日志：

```bash
systemctl status yinli-backend nginx --no-pager
journalctl -u yinli-backend -f
tail -f /var/log/nginx/access.log /var/log/nginx/error.log
```

更新到新的已审核版本：

```bash
systemctl stop yinli-backend
tar -C /var/lib -czf /root/yinli-data-before-update.tar.gz yinli

cd /opt/yinli/app
runuser -u yinli -- git fetch --all --tags
runuser -u yinli -- git checkout <NEW_RELEASE_REF>

cd backend
runuser -u yinli -- uv sync --frozen
cd ../frontend
runuser -u yinli -- npm ci
runuser -u yinli -- npm run build

systemctl start yinli-backend
nginx -t && systemctl reload nginx
curl -fsS http://127.0.0.1:8000/api/health
```

SQLite 结构升级采用启动时原地变更。更新前必须备份 `/var/lib/yinli`；仅切回旧代码不一定能回滚数据结构，必要时同时恢复备份。

## 12. 停止公网测试

测试结束后，先从云安全组删除 TCP `8000` 入站规则，再执行：

```bash
systemctl disable --now nginx yinli-backend
```

数据保留在 `/var/lib/yinli`。确认不再需要前不要删除该目录。

## 13. 已知限制

- HTTP 流量为明文，模型输入、输出、上传文件和 API 请求可能被窃听或篡改。
- 没有用户认证或租户隔离；知道地址的人都可以访问。
- Debian 上当前使用 `AGENT_WORKSTATION_SECRET_STORE=memory` 兼容环境变量 Key；设置页面写入的新 Key 不可跨服务重启保存。
- DockerWorkspace 需要另行安装 Docker、把 `yinli` 加入受控 Docker 权限并完成真实 Runtime 门禁。Docker 组近似 root 权限，不能在未评审前直接启用。
- 当前指南不包含自动备份、监控、TLS、域名、限流、WAF 或高可用。
