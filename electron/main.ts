/**
 * Electron 主进程入口
 *
 * 职责：
 * 1. 创建浏览器窗口并加载前端应用
 * 2. 管理 Python 后端子进程（开发模式连接已有服务，生产模式启动子进程）
 * 3. 处理应用生命周期事件（ready / window-all-closed / activate）
 * 4. 注册 IPC 处理器，桥接前端与系统能力
 */

import { app, BrowserWindow, ipcMain, shell } from 'electron';
import * as path from 'path';
import { spawn, ChildProcess } from 'child_process';

/** 当前是否处于开发模式（Vite dev server 运行中） */
const isDev = process.env.NODE_ENV === 'development';

/** Vite 开发服务器地址 */
const DEV_SERVER_URL = 'http://localhost:5173';

/** Python 后端子进程引用 */
let pythonProcess: ChildProcess | null = null;

/** 主窗口引用 */
let mainWindow: BrowserWindow | null = null;

/**
 * 创建主浏览器窗口
 * 配置窗口尺寸、无边框标题栏（自定义 TitleBar）、预加载脚本
 */
function createMainWindow(): void {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 800,
    minWidth: 800,
    minHeight: 600,
    titleBarStyle: 'hiddenInset', // macOS 内嵌标题栏，留出红绿灯空间
    trafficLightPosition: { x: 16, y: 16 }, // 红绿灯按钮位置
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true, // 安全：隔离渲染进程上下文
      nodeIntegration: false, // 安全：禁用 Node 集成
    },
  });

  // 开发模式连接 Vite dev server，生产模式加载打包后的 HTML
  if (isDev) {
    mainWindow.loadURL(DEV_SERVER_URL);
    mainWindow.webContents.openDevTools({ mode: 'detach' });
  } else {
    mainWindow.loadFile(path.join(__dirname, '../frontend/dist/index.html'));
  }

  // 外部链接用系统浏览器打开
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

/**
 * 启动 Python 后端子进程
 * 生产模式下作为 Electron 子进程运行，随主进程启动/关闭
 */
function startPythonBackend(): void {
  if (isDev) {
    // 开发模式：后端独立运行，不启动子进程
    console.log('[Electron] Dev mode: skipping Python backend launch');
    return;
  }

  const backendPath = path.join(__dirname, '../backend');
  const pythonBin = path.join(backendPath, '.venv/bin/python');

  pythonProcess = spawn(pythonBin, ['-m', 'uvicorn', 'src.server.main:app', '--port', '8765'], {
    cwd: backendPath,
    env: { ...process.env, PYTHONUNBUFFERED: '1' },
    stdio: ['pipe', 'pipe', 'pipe'],
  });

  pythonProcess.stdout?.on('data', (data: Buffer) => {
    console.log(`[Python] ${data.toString().trim()}`);
  });

  pythonProcess.stderr?.on('data', (data: Buffer) => {
    console.error(`[Python] ${data.toString().trim()}`);
  });

  pythonProcess.on('exit', (code: number | null) => {
    console.log(`[Python] Process exited with code ${code}`);
    pythonProcess = null;
  });
}

/**
 * 停止 Python 后端子进程
 */
function stopPythonBackend(): void {
  if (pythonProcess) {
    pythonProcess.kill('SIGTERM');
    pythonProcess = null;
  }
}

// ========== App Lifecycle ==========

app.whenReady().then(() => {
  startPythonBackend();
  createMainWindow();

  // macOS: 点击 Dock 图标时重新创建窗口
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createMainWindow();
    }
  });
});

// 所有窗口关闭时退出应用（macOS 除外）
app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    stopPythonBackend();
    app.quit();
  }
});

// 应用退出前清理子进程
app.on('before-quit', () => {
  stopPythonBackend();
});

// ========== IPC Handlers ==========

/**
 * 获取 Python 后端服务地址
 * 前端通过此 IPC 获取 API base URL
 */
ipcMain.handle('get-backend-url', () => {
  return 'http://localhost:8765';
});

/**
 * 获取应用版本号
 */
ipcMain.handle('get-app-version', () => {
  return app.getVersion();
});
