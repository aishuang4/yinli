#!/bin/bash
# Electron 开发模式启动脚本
#
# 职责：
# 1. 确保后端 FastAPI 和前端 Vite 开发服务可用
# 2. 编译 Electron 主进程与 preload 脚本
# 3. 启动 Electron 桌面窗口加载 Vite 开发服务

set -e

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND_URL="http://localhost:8765"
FRONTEND_URL="http://localhost:5173"
BACKEND_PID=""
FRONTEND_PID=""

# Electron 42 / Vite 8 需要 Node 20.19+。开发机若默认是 Node 18，
# 优先切 nvm Node 24；Codex 环境下再回退到捆绑 Node。
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

cleanup() {
  if [ -n "$BACKEND_PID" ]; then
    kill "$BACKEND_PID" 2>/dev/null || true
  fi
  if [ -n "$FRONTEND_PID" ]; then
    kill "$FRONTEND_PID" 2>/dev/null || true
  fi
}

wait_for_url() {
  local url="$1"
  local label="$2"
  local max_attempts=40

  for attempt in $(seq 1 "$max_attempts"); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      echo "   $label 已就绪"
      return 0
    fi
    sleep 1
  done

  echo "   $label 启动超时：$url"
  return 1
}

trap cleanup EXIT INT TERM

echo "启动 Agent Workstation Electron 开发窗口..."

cd "$ROOT_DIR/backend"
if curl -fsS "$BACKEND_URL/api/health" >/dev/null 2>&1; then
  echo "后端已运行：$BACKEND_URL"
else
  echo "启动后端 FastAPI..."
  uv run python -m uvicorn src.server.main:app --reload --reload-dir src --port 8765 &
  BACKEND_PID=$!
  wait_for_url "$BACKEND_URL/api/health" "后端"
fi

cd "$ROOT_DIR/frontend"
if curl -fsS "$FRONTEND_URL" >/dev/null 2>&1; then
  echo "前端已运行：$FRONTEND_URL"
else
  echo "启动 Vite 前端..."
  npm run dev &
  FRONTEND_PID=$!
  wait_for_url "$FRONTEND_URL" "前端"
fi

echo "编译 Electron 主进程..."
npm run electron:compile

echo "启动 Electron..."
NODE_ENV=development ./node_modules/.bin/electron electron-dist/main.js
