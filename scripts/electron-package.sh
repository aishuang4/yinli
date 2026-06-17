#!/bin/bash
# Electron 桌面端打包脚本
#
# 用法：
#   npm run electron:pack        # 当前平台打包
#   npm run electron:build:mac   # macOS 产物
#   npm run electron:build:win   # Windows 产物

set -e

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TARGET="${1:-}"

export NVM_DIR="$HOME/.nvm"
if [ -s "$NVM_DIR/nvm.sh" ]; then
  # shellcheck source=/dev/null
  . "$NVM_DIR/nvm.sh"
  nvm use 24 >/dev/null 2>&1 || true
fi

if command -v node >/dev/null 2>&1; then
  NODE_MAJOR="$(node -p "Number(process.versions.node.split('.')[0])")"
else
  NODE_MAJOR=0
fi

if [ "$NODE_MAJOR" -lt 20 ] && [ -x "$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node" ]; then
  export PATH="$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin:$PATH"
fi

cd "$ROOT_DIR/frontend"

echo "构建前端..."
npm run build

echo "编译 Electron 主进程..."
npm run electron:compile

case "$TARGET" in
  mac)
    echo "打包 macOS 桌面端..."
    npx electron-builder --mac
    ;;
  win)
    echo "打包 Windows 桌面端..."
    npx electron-builder --win
    ;;
  "")
    echo "打包当前平台桌面端..."
    npx electron-builder
    ;;
  *)
    echo "未知打包目标：$TARGET"
    echo "支持目标：mac、win，或不传目标打包当前平台。"
    exit 1
    ;;
esac
