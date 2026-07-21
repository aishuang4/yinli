#!/bin/bash
# 生成 Electron macOS 图标与 DMG 背景。

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BUILD_DIR="$ROOT_DIR/frontend/build"
ICONSET_DIR="$BUILD_DIR/icon.iconset"
GENERATOR_BIN="$(mktemp /private/tmp/agent-workstation-assets.XXXXXX)"

cleanup() {
  rm -f "$GENERATOR_BIN"
}
trap cleanup EXIT

mkdir -p "$BUILD_DIR"
clang -fobjc-arc -fblocks \
  -framework AppKit \
  -framework Foundation \
  "$ROOT_DIR/scripts/generate-macos-assets.m" \
  -o "$GENERATOR_BIN"
"$GENERATOR_BIN" "$BUILD_DIR"

rm -rf "$ICONSET_DIR"
mkdir -p "$ICONSET_DIR"

for size in 16 32 64 128 256 512; do
  sips -z "$size" "$size" "$BUILD_DIR/icon-1024.png" \
    --out "$ICONSET_DIR/icon-${size}.png" >/dev/null
done
cp "$BUILD_DIR/icon-1024.png" "$ICONSET_DIR/icon-1024.png"

node "$ROOT_DIR/scripts/create-icns.mjs" "$BUILD_DIR/icon.icns" \
  "icp4=$ICONSET_DIR/icon-16.png" \
  "icp5=$ICONSET_DIR/icon-32.png" \
  "ic11=$ICONSET_DIR/icon-32.png" \
  "icp6=$ICONSET_DIR/icon-64.png" \
  "ic12=$ICONSET_DIR/icon-64.png" \
  "ic07=$ICONSET_DIR/icon-128.png" \
  "ic08=$ICONSET_DIR/icon-256.png" \
  "ic13=$ICONSET_DIR/icon-256.png" \
  "ic09=$ICONSET_DIR/icon-512.png" \
  "ic14=$ICONSET_DIR/icon-512.png" \
  "ic10=$ICONSET_DIR/icon-1024.png"
echo "macOS 应用图标已输出到 frontend/build/icon.icns"

node "$ROOT_DIR/scripts/create-ico.mjs" "$BUILD_DIR/icon.ico" \
  "16=$ICONSET_DIR/icon-16.png" \
  "32=$ICONSET_DIR/icon-32.png" \
  "64=$ICONSET_DIR/icon-64.png" \
  "128=$ICONSET_DIR/icon-128.png" \
  "256=$ICONSET_DIR/icon-256.png"
echo "Windows 应用图标已输出到 frontend/build/icon.ico"
