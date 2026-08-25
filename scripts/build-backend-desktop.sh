#!/bin/bash
# 构建桌面端内置后端可执行文件
#
# 说明：
# - 该脚本需要在目标平台分别执行，macOS 构建 macOS 可执行文件，Windows 构建 .exe。
# - 产物输出到 backend/dist/backend-server，后续可复制到 Electron extraResources。

set -e

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR/backend"

echo "构建桌面端后端可执行文件..."
uv sync
uv run --with pyinstaller pyinstaller --clean --noconfirm backend-server.spec

echo "后端可执行文件已输出到 backend/dist/"
