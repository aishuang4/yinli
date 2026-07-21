#!/bin/bash
# 开发模式启动脚本 — 同时启动前端 Vite 和后端 FastAPI
#
# 用法: ./scripts/dev.sh

set -e

echo "🚀 Starting 引力 YINLI in development mode..."
echo ""

# 确保使用 Node 24
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
nvm use 24 2>/dev/null || echo "⚠️  Using system Node: $(node --version)"

# 启动后端（后台）
echo "📦 Starting Python backend..."
cd "$(dirname "$0")/../backend"
uv run python -m uvicorn src.server.main:app --reload --reload-dir src --port 8765 &
BACKEND_PID=$!
echo "   Backend PID: $BACKEND_PID"
cd ..

# 等待后端启动
sleep 2

# 启动前端
echo "🎨 Starting Vite frontend..."
cd frontend
npm run dev &
FRONTEND_PID=$!
echo "   Frontend PID: $FRONTEND_PID"

echo ""
echo "✅ Development servers started!"
echo "   Frontend: http://localhost:5173"
echo "   Backend:  http://localhost:8765"
echo "   API Docs: http://localhost:8765/docs"
echo ""
echo "Press Ctrl+C to stop both servers."

# 捕获退出信号，清理子进程
trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit" INT TERM
wait
