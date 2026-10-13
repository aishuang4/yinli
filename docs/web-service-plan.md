# Web 托管剩余事项

> 状态：2026-10-13。租户隔离、平台模型转发和沙箱约束已经写进代码。在线事实见 `docs/architecture-overview.md`「托管账号与桌面登录」。

桌面登录已经接到顶栏：令牌进本机密钥存储，不进 `models.json`。平台模型经云端 `/api/platform/v1` 进入完整 Agent 运行时。退出或令牌过期后，平台模型从列表消失，本机历史和本机自配模型仍在。

这些约束继续有效：Web 与桌面不同步会话和文件；平台 Key 不下发到桌面；同一邮箱可以同时在两端登录，数据仍然各管各的。桌面安装包的签名和公证见 `docs/electron-packaging-plan.md`。

还没在干净机器上验收的是部署：

- 数据目录改为宿主 `/srv/yinli/data`，以同一路径挂进容器。旧命名卷不迁移，步骤见 `deploy/BACKUP.md`。
- 沙箱用 gVisor `runsc` 和独立网络，步骤见 `deploy/SANDBOX.md`。
- 托管缺少主密钥、key2api 服务令牌或沙箱 Docker 地址时会拒绝启动。
