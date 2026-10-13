# 托管沙箱

托管命令只在容器里执行。方案 A 和方案 B 用同一套 `AGENT_WORKSTATION_SANDBOX_*`，差别只在 daemon 地址和证书。

## 方案 A：本机 Docker

1. 安装 gVisor，并在 `/etc/docker/daemon.json` 注册 runtime：

```json
{
  "runtimes": {
    "runsc": {
      "path": "/usr/bin/runsc"
    }
  }
}
```

改完后重启 Docker。

2. 建沙箱网络。关掉容器之间的互访：

```bash
docker network create --driver bridge \
  -o com.docker.network.bridge.enable_icc=false \
  yinli-sandbox
```

3. 在宿主 `DOCKER-USER` 链拒绝这个网段访问内网和宿主机。先用 `docker network inspect yinli-sandbox` 看网段，下面用 `172.28.0.0/16` 作例子：

```bash
iptables -I DOCKER-USER -s 172.28.0.0/16 -d 10.0.0.0/8 -j DROP
iptables -I DOCKER-USER -s 172.28.0.0/16 -d 172.16.0.0/12 -j DROP
iptables -I DOCKER-USER -s 172.28.0.0/16 -d 192.168.0.0/16 -j DROP
iptables -I DOCKER-USER -s 172.28.0.0/16 -d 169.254.0.0/16 -j DROP
iptables -I DOCKER-USER -s 172.28.0.0/16 -d 100.64.0.0/10 -j DROP
iptables -I DOCKER-USER -s 172.28.0.0/16 -d <宿主地址> -j DROP
```

4. `deploy/docker-compose.web.yml` 把 `/srv/yinli/data` 以同一路径挂进后端，并挂上 `/var/run/docker.sock`。`SANDBOX_HOST_DATA_ROOT` 必须等于这个路径。

`runsc` 是默认 runtime。改成 `runc` 时必须同时设 `AGENT_WORKSTATION_SANDBOX_ALLOW_RUNC=true`，否则托管拒绝启动。

## 方案 B：独立沙箱主机

后端不挂 docker.sock，改为：

```bash
AGENT_WORKSTATION_SANDBOX_DOCKER_HOST=tcp://sandbox-host:2376
AGENT_WORKSTATION_SANDBOX_TLS_CA=/srv/yinli/certs/ca.pem
AGENT_WORKSTATION_SANDBOX_TLS_CERT=/srv/yinli/certs/cert.pem
AGENT_WORKSTATION_SANDBOX_TLS_KEY=/srv/yinli/certs/key.pem
```

沙箱主机要能以 `SANDBOX_HOST_DATA_ROOT` 看到同一份租户文件（NFS 或其他共享存储）。证书可用：

```bash
openssl req -newkey rsa:4096 -nodes -keyout key.pem -out cert.csr
openssl x509 -req -in cert.csr -CA ca.pem -CAkey ca-key.pem -out cert.pem -days 365
```

把 CA、服务端证书和客户端证书按 Docker TLS 的要求放到沙箱主机和后端能读到的路径。
