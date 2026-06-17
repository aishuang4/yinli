"""
桌面端后端入口

供 PyInstaller/Nuitka 打包成平台可执行文件。Electron 生产模式会启动该入口，
并通过环境变量传入用户数据目录、数据库路径和端口。
"""

import argparse
import os

import uvicorn


def main() -> None:
    """启动桌面端内置 FastAPI 服务。"""
    parser = argparse.ArgumentParser(description="Agent Workstation desktop backend")
    parser.add_argument("--host", default=os.getenv("AGENT_WORKSTATION_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("AGENT_WORKSTATION_PORT", "8765")))
    args = parser.parse_args()

    os.environ.setdefault("AGENT_WORKSTATION_DESKTOP_MODE", "1")
    uvicorn.run(
        "src.server.main:app",
        host=args.host,
        port=args.port,
        log_level="info",
        factory=False,
    )


if __name__ == "__main__":
    main()
