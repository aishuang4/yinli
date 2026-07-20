/**
 * Electron 主进程入口
 *
 * 职责：
 * 1. 创建浏览器窗口并加载前端应用
 * 2. 管理 Python 后端子进程（开发模式连接已有服务，生产模式启动子进程）
 * 3. 处理应用生命周期事件（ready / window-all-closed / activate）
 * 4. 注册 IPC 处理器，桥接前端与系统能力
 */

import { app, BrowserWindow, dialog, ipcMain, shell } from 'electron';
import * as path from 'path';
import * as fs from 'fs';
import { spawn, ChildProcess } from 'child_process';

/** 当前是否处于开发模式（Vite dev server 运行中） */
const isDev = process.env.NODE_ENV === 'development';

// 安装版固定 userData 目录名，避免 productName 或 package name 变化造成数据分散。
if (!isDev) {
  app.setPath('userData', path.join(app.getPath('appData'), 'agent-workstation-desktop'));
}

/** Vite 开发服务器地址 */
const DEV_SERVER_URL = 'http://localhost:5173';
const BACKEND_SERVER_URL = 'http://127.0.0.1:8765';

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
    mainWindow.loadFile(path.join(__dirname, '../dist/index.html'));
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

  const backendProcess = resolvePackagedBackendProcess();
  if (!backendProcess) {
    console.error('[Electron] No packaged backend entry found');
    return;
  }

  const userDataPath = app.getPath('userData');
  const dataPath = path.join(userDataPath, 'data');
  const logsPath = path.join(userDataPath, 'logs');
  fs.mkdirSync(dataPath, { recursive: true });
  fs.mkdirSync(logsPath, { recursive: true });

  const env = {
    ...process.env,
    PYTHONUNBUFFERED: '1',
    AGENT_WORKSTATION_DESKTOP_MODE: '1',
    AGENT_WORKSTATION_DATA_DIR: userDataPath,
    AGENT_WORKSTATION_DATABASE_URL: `sqlite+aiosqlite:///${path.join(dataPath, 'agent_workstation.db')}`,
    AGENT_WORKSTATION_PORT: '8765',
    AGENT_WORKSTATION_CORS_ORIGINS: '["http://localhost:5173","http://localhost:3000","null","file://"]',
  };

  pythonProcess = spawn(backendProcess.command, backendProcess.args, {
    cwd: backendProcess.cwd,
    env,
    stdio: ['pipe', 'pipe', 'pipe'],
    windowsHide: true,
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

interface BackendProcessConfig {
  command: string;
  args: string[];
  cwd: string;
}

function resolvePackagedBackendProcess(): BackendProcessConfig | null {
  const resourcesPath = process.resourcesPath;
  const executableName = process.platform === 'win32' ? 'backend-server.exe' : 'backend-server';
  const executableCandidates = [
    path.join(resourcesPath, 'backend-server', executableName),
    path.join(resourcesPath, executableName),
  ];

  for (const candidate of executableCandidates) {
    if (fs.existsSync(candidate)) {
      return {
        command: candidate,
        args: ['--host', '127.0.0.1', '--port', '8765'],
        cwd: path.dirname(candidate),
      };
    }
  }

  const backendPath = path.join(resourcesPath, 'backend');
  if (!fs.existsSync(backendPath)) {
    return null;
  }

  const venvPython = process.platform === 'win32'
    ? path.join(backendPath, '.venv', 'Scripts', 'python.exe')
    : path.join(backendPath, '.venv', 'bin', 'python');

  if (fs.existsSync(venvPython)) {
    return {
      command: venvPython,
      args: ['-m', 'uvicorn', 'src.server.main:app', '--host', '127.0.0.1', '--port', '8765'],
      cwd: backendPath,
    };
  }

  const uvCommand = process.platform === 'win32' ? 'uv.cmd' : 'uv';
  return {
    command: uvCommand,
    args: ['run', 'python', '-m', 'uvicorn', 'src.server.main:app', '--host', '127.0.0.1', '--port', '8765'],
    cwd: backendPath,
  };
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

async function waitForPythonBackend(timeoutMs = 15000): Promise<boolean> {
  if (isDev) {
    return true;
  }
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`${BACKEND_SERVER_URL}/api/health`, { cache: 'no-store' });
      if (response.ok) {
        return true;
      }
    } catch {
      // 后端可执行文件首次启动需要解压依赖，短暂等待后重试。
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  console.error('[Electron] Python backend did not become ready before timeout');
  return false;
}

// ========== App Lifecycle ==========

app.whenReady().then(async () => {
  startPythonBackend();
  await waitForPythonBackend();
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
  return BACKEND_SERVER_URL;
});

/**
 * 获取应用版本号
 */
ipcMain.handle('get-app-version', () => {
  return app.getVersion();
});

/**
 * 选择 Project 工作目录。目录名直接作为 Project 名称，路径由主进程读取。
 */
ipcMain.handle('select-project-directory', async () => {
  const options = {
    title: '选择 Project 文件夹',
    properties: ['openDirectory', 'createDirectory'] as Array<'openDirectory' | 'createDirectory'>,
  };
  const result = mainWindow
    ? await dialog.showOpenDialog(mainWindow, options)
    : await dialog.showOpenDialog(options);
  if (result.canceled || result.filePaths.length === 0) {
    return { canceled: true };
  }
  const selectedPath = path.resolve(result.filePaths[0]);
  if (!fs.existsSync(selectedPath) || !fs.statSync(selectedPath).isDirectory()) {
    return { canceled: true };
  }
  return {
    canceled: false,
    path: selectedPath,
    name: path.basename(selectedPath),
  };
});

/**
 * 打开本地路径。仅允许当前工作目录、用户主目录和 Electron userData 下的路径。
 */
ipcMain.handle('open-path', async (_event, targetPath: string) => {
  const resolvedPath = resolveSafeLocalPath(targetPath);
  if (!resolvedPath) {
    return { ok: false, error: 'Path is not allowed' };
  }
  const error = await shell.openPath(resolvedPath);
  if (error) {
    return { ok: false, error };
  }
  return { ok: true };
});

/**
 * 打开外部链接。当前只允许 Obsidian vault 深链。
 */
ipcMain.handle('open-external', async (_event, url: string) => {
  if (!isAllowedExternalUrl(url)) {
    return { ok: false, error: 'URL is not allowed' };
  }
  await shell.openExternal(url);
  return { ok: true };
});

function resolveSafeLocalPath(targetPath: string): string | null {
  if (!targetPath || typeof targetPath !== 'string') {
    return null;
  }
  const resolved = path.resolve(targetPath);
  const allowedRoots = [
    process.cwd(),
    app.getPath('userData'),
    app.getPath('home'),
  ].map((item) => path.resolve(item));
  const isAllowed = allowedRoots.some((root) => (
    resolved === root || resolved.startsWith(root + path.sep)
  ));
  if (!isAllowed || !fs.existsSync(resolved)) {
    return null;
  }
  return resolved;
}

function isAllowedExternalUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return url.protocol === 'obsidian:' && url.hostname === 'open';
  } catch {
    return false;
  }
}
