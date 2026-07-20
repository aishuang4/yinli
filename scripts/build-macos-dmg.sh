#!/bin/bash
# 从已构建的 .app 生成带拖拽引导和 Finder 固定布局的 DMG。

set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "用法：$0 <app-path> <output-dmg>"
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP_PATH="$1"
OUTPUT_DMG="$2"
VOLUME_NAME="Agent Workstation"
STAGING_DIR="$(mktemp -d /private/tmp/agent-workstation-dmg.XXXXXX)"
MOUNT_DIR=""
RW_DMG="$STAGING_DIR/agent-workstation-rw.dmg"

cleanup() {
  if [ -n "$MOUNT_DIR" ]; then
    hdiutil detach "$MOUNT_DIR" -quiet >/dev/null 2>&1 || true
  fi
  rm -rf "$STAGING_DIR"
}
trap cleanup EXIT

mkdir -p "$STAGING_DIR/payload/.background"
ditto "$APP_PATH" "$STAGING_DIR/payload/Agent Workstation.app"
cp "$ROOT_DIR/frontend/build/dmg-background.png" "$STAGING_DIR/payload/.background/background.png"
ln -s /Applications "$STAGING_DIR/payload/Applications"

hdiutil create \
  -volname "$VOLUME_NAME" \
  -srcfolder "$STAGING_DIR/payload" \
  -ov \
  -format UDRW \
  "$RW_DMG" >/dev/null

ATTACH_OUTPUT="$(hdiutil attach "$RW_DMG" -readwrite -noverify -noautoopen)"
MOUNT_DIR="$(printf '%s\n' "$ATTACH_OUTPUT" | awk -F '\t' 'END { print $NF }')"
sleep 1

osascript - "$VOLUME_NAME" <<'APPLESCRIPT'
on run argv
  set volumeName to item 1 of argv
  tell application "Finder"
    tell disk volumeName
      open
      delay 1
      tell container window
        set current view to icon view
        set toolbar visible to false
        set statusbar visible to false
        set pathbar visible to false
        set bounds to {120, 120, 840, 560}
      end tell
      set backgroundFile to file ".background:background.png"
      set viewOptions to icon view options of container window
      set arrangement of viewOptions to not arranged
      set icon size of viewOptions to 112
      set text size of viewOptions to 13
      set background picture of viewOptions to backgroundFile
      set position of item "Agent Workstation.app" to {180, 245}
      set position of item "Applications" to {540, 245}
      update without registering applications
      delay 2
      close
    end tell
  end tell
end run
APPLESCRIPT

sync
hdiutil detach "$MOUNT_DIR" -quiet
hdiutil convert "$RW_DMG" -format UDZO -imagekey zlib-level=9 -o "$OUTPUT_DMG" -ov >/dev/null
echo "macOS DMG 已输出到 $OUTPUT_DMG"
