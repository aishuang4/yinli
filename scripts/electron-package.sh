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
  unset npm_config_prefix
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

if [ "$TARGET" = "win" ]; then
  case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*) ;;
    *)
      echo "Windows 安装包必须在 Windows 原生环境构建，以确保内置 backend-server.exe 与目标系统匹配。"
      exit 1
      ;;
  esac
fi

package_macos() {
  local version package_arch app_path zip_path dmg_path

  "$ROOT_DIR/scripts/generate-macos-assets.sh"
  npx electron-builder --projectDir desktop-package --config ../electron-builder.yml --mac dir

  version="$(node -p "require('./package.json').version")"
  package_arch="$(node -p "process.arch")"
  app_path="$ROOT_DIR/release/mac-${package_arch}/引力 YINLI.app"
  zip_path="$ROOT_DIR/release/引力-YINLI-${version}-mac-${package_arch}.zip"
  dmg_path="$ROOT_DIR/release/引力-YINLI-${version}-mac-${package_arch}.dmg"

  echo "生成 macOS ZIP..."
  ditto -c -k --sequesterRsrc --keepParent "$app_path" "$zip_path"

  echo "生成带拖拽引导的 macOS DMG..."
  "$ROOT_DIR/scripts/build-macos-dmg.sh" "$app_path" "$dmg_path"
}

echo "构建桌面端后端..."
"$ROOT_DIR/scripts/build-backend-desktop.sh"

echo "构建前端..."
npm run build

echo "编译 Electron 主进程..."
npm run electron:compile

case "$TARGET" in
  mac)
    echo "打包 macOS 桌面端..."
    package_macos
    ;;
  win)
    echo "打包 Windows 桌面端..."
    npx electron-builder --projectDir desktop-package --config ../electron-builder.yml --win
    ;;
  "")
    echo "打包当前平台桌面端..."
    if [ "$(uname -s)" = "Darwin" ]; then
      package_macos
    else
      npx electron-builder --projectDir desktop-package --config ../electron-builder.yml
    fi
    ;;
  *)
    echo "未知打包目标：$TARGET"
    echo "支持目标：mac、win，或不传目标打包当前平台。"
    exit 1
    ;;
esac
