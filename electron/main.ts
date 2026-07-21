/**
 * Electron 主进程入口
 *
 * 职责：
 * 1. 创建浏览器窗口并加载前端应用
 * 2. 管理 Python 后端子进程（开发模式连接已有服务，生产模式启动子进程）
 * 3. 处理应用生命周期事件（ready / window-all-closed / activate）
 * 4. 注册 IPC 处理器，桥接前端与系统能力
 */

import { app, BrowserWindow, dialog, ipcMain, Menu, shell } from 'electron';
import * as path from 'path';
import * as fs from 'fs';
import * as net from 'net';
import { spawn, ChildProcess } from 'child_process';
import { randomUUID } from 'crypto';

/** 当前是否处于开发模式（Vite dev server 运行中） */
const isDev = process.env.NODE_ENV === 'development';
/** Windows 安装包排障时由调试脚本开启，允许查看渲染进程 DevTools。 */
const desktopDebugEnabled = process.env.AGENT_WORKSTATION_DESKTOP_DEBUG === '1';

/** 安装版在用户主目录下统一保存应用数据，便于用户备份与排障。 */
const DESKTOP_USER_DATA_DIR = path.join(app.getPath('home'), '.agent-workstation');
/** 旧版按 Electron 默认目录保存数据，升级时需要迁移。 */
const LEGACY_DESKTOP_USER_DATA_DIR = path.join(app.getPath('appData'), 'agent-workstation-desktop');

/**
 * 将旧安装版数据迁移到稳定且可见的用户目录。
 * 目标已存在时将其视为权威目录，避免覆盖用户已经迁出的数据。
 */
function migrateLegacyDesktopUserData(): void {
  if (!fs.existsSync(LEGACY_DESKTOP_USER_DATA_DIR) || fs.existsSync(DESKTOP_USER_DATA_DIR)) {
    return;
  }
  try {
    fs.renameSync(LEGACY_DESKTOP_USER_DATA_DIR, DESKTOP_USER_DATA_DIR);
  } catch (error) {
    console.warn('[Electron] Failed to migrate legacy desktop data', error);
  }
}

// 安装版固定 userData 目录名，避免 productName 或 package name 变化造成数据分散。
if (!isDev) {
  migrateLegacyDesktopUserData();
  app.setPath('userData', DESKTOP_USER_DATA_DIR);
}

/** Vite 开发服务器地址 */
const DEV_SERVER_URL = 'http://localhost:5173';
const DEV_BACKEND_SERVER_URL = 'http://127.0.0.1:8765';
/** 安装版从较少被占用的高位端口段中选择本轮可用端口。 */
const DESKTOP_BACKEND_PORT_START = 39100;
const DESKTOP_BACKEND_PORT_COUNT = 200;

/** Python 后端子进程引用 */
let pythonProcess: ChildProcess | null = null;

/** 主窗口引用 */
let mainWindow: BrowserWindow | null = null;

type DesktopBackendState = 'starting' | 'ready' | 'error';

interface DesktopBackendStatus {
  state: DesktopBackendState;
  detail?: string;
}

interface BackendOwnerRecord {
  pid: number;
  instanceId: string;
  startedAt: string;
  /** 未携带该字段的旧记录按历史固定端口 8765 处理。 */
  port?: number;
}

let backendStatus: DesktopBackendStatus = { state: 'starting' };
let backendStartupPromise: Promise<boolean> | null = null;
let backendInstanceId: string | null = null;
let backendPort: number | null = isDev ? 8765 : null;
let appQuitting = false;

/** 返回当前运行期后端地址。开发模式仍使用固定的 Vite 代理端口。 */
function backendServerUrl(port = backendPort): string {
  return `http://127.0.0.1:${port ?? 8765}`;
}

/** 返回安装实例的诊断日志目录，并在首次使用时创建。 */
function desktopLogsPath(): string {
  const logsPath = path.join(app.getPath('userData'), 'logs');
  fs.mkdirSync(logsPath, { recursive: true });
  return logsPath;
}

/** 将桌面端关键运行信息写入本地日志，便于无控制台的安装包排障。 */
function appendDesktopLog(fileName: string, message: string): void {
  try {
    const line = `[${new Date().toISOString()}] ${message.trim()}\n`;
    fs.appendFileSync(path.join(desktopLogsPath(), fileName), line, 'utf8');
  } catch {
    // 日志写入失败不能影响应用本身的启动与退出。
  }
}

/** 更新后端状态，并通知已加载的渲染进程。 */
function setBackendStatus(status: DesktopBackendStatus): void {
  backendStatus = status;
  appendDesktopLog('electron-main.log', `Backend status: ${status.state}${status.detail ? ` - ${status.detail}` : ''}`);
  mainWindow?.webContents.send('backend-status', status);
}

/** 返回当前安装实例用于清理遗留进程的本地记录路径。 */
function backendOwnerPath(): string {
  return path.join(app.getPath('userData'), 'backend-process.json');
}

function readBackendOwner(): BackendOwnerRecord | null {
  try {
    const raw = fs.readFileSync(backendOwnerPath(), 'utf8');
    const parsed = JSON.parse(raw) as Partial<BackendOwnerRecord>;
    if (typeof parsed.pid !== 'number' || typeof parsed.instanceId !== 'string') {
      return null;
    }
    return {
      pid: parsed.pid,
      instanceId: parsed.instanceId,
      startedAt: typeof parsed.startedAt === 'string' ? parsed.startedAt : '',
      port: typeof parsed.port === 'number' ? parsed.port : 8765,
    };
  } catch {
    return null;
  }
}

function writeBackendOwner(record: BackendOwnerRecord): void {
  fs.writeFileSync(backendOwnerPath(), JSON.stringify(record), 'utf8');
}

function removeBackendOwner(expectedPid?: number): void {
  const owner = readBackendOwner();
  if (expectedPid !== undefined && owner?.pid !== expectedPid) {
    return;
  }
  try {
    fs.unlinkSync(backendOwnerPath());
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== 'ENOENT') {
      console.warn('[Electron] Failed to remove backend owner record', error);
    }
  }
}

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
    // macOS 保持红绿灯区域；Windows 隐藏系统标题栏并将窗口控制按钮覆盖在内容之上，
    // 使工作区能够从窗口左上角开始，与网页端保持一致。
    titleBarStyle: process.platform === 'darwin' ? 'hiddenInset' : 'hidden',
    ...(process.platform === 'win32' ? {
      titleBarOverlay: {
        color: '#00000000',
        symbolColor: '#5f6773',
        height: 32,
      },
    } : {}),
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
    if (desktopDebugEnabled) {
      mainWindow.webContents.openDevTools({ mode: 'detach' });
    }
  }

  // 外部链接用系统浏览器打开
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });

  // macOS 的 Command + W 仅隐藏窗口，保留渲染进程和当前工作状态；
  // Command + Q 触发 appQuitting 后才允许窗口真正销毁并退出应用。
  mainWindow.on('close', (event) => {
    if (process.platform === 'darwin' && !appQuitting) {
      event.preventDefault();
      mainWindow?.hide();
    }
  });
}

/** 请求本机健康接口；失败时返回 null。 */
async function fetchBackendHealth(port?: number): Promise<{ desktopInstanceId?: string | null } | null> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 1000);
  try {
    const response = await fetch(`${backendServerUrl(port)}/api/health`, {
      cache: 'no-store',
      signal: controller.signal,
    });
    if (!response.ok) {
      return null;
    }
    return await response.json() as { desktopInstanceId?: string | null };
  } catch {
    return null;
  } finally {
    clearTimeout(timeout);
  }
}

/** 在 Windows 结束整个进程树，避免打包后端遗留子进程占用端口。 */
async function terminateProcessTree(pid: number): Promise<void> {
  if (process.platform === 'win32') {
    await new Promise<void>((resolve) => {
      const taskkill = spawn('taskkill', ['/pid', String(pid), '/T', '/F'], { windowsHide: true });
      taskkill.once('error', () => resolve());
      taskkill.once('exit', () => resolve());
    });
    return;
  }

  try {
    process.kill(pid, 'SIGTERM');
  } catch {
    return;
  }

  await new Promise((resolve) => setTimeout(resolve, 300));
  try {
    process.kill(pid, 0);
    process.kill(pid, 'SIGKILL');
  } catch {
    // 进程已自然退出。
  }
}

/**
 * 清理因崩溃或强制结束而遗留的本应用后端。
 * 仅当健康接口返回的实例标识与记录匹配时才会终止，避免误杀其他本机服务。
 */
async function cleanupStalePythonBackend(): Promise<void> {
  if (isDev) {
    return;
  }

  const owner = readBackendOwner();
  if (!owner) {
    return;
  }

  const health = await fetchBackendHealth(owner.port);
  if (health?.desktopInstanceId !== owner.instanceId) {
    removeBackendOwner(owner.pid);
    return;
  }

  console.log(`[Electron] Cleaning stale Python backend ${owner.pid}`);
  appendDesktopLog('electron-main.log', `Cleaning stale backend process ${owner.pid}`);
  await terminateProcessTree(owner.pid);
  removeBackendOwner(owner.pid);
  await new Promise((resolve) => setTimeout(resolve, 200));
}

/**
 * 在安装版私有高位端口段内选择空闲端口。
 * 端口只绑定本机回环地址，避免与局域网服务发生冲突；后端启动前的短暂竞态由
 * 进程启动失败处理兜底，下一次重试会重新选择端口。
 */
async function findAvailableBackendPort(): Promise<number | null> {
  const offset = Math.floor(Math.random() * DESKTOP_BACKEND_PORT_COUNT);
  for (let index = 0; index < DESKTOP_BACKEND_PORT_COUNT; index += 1) {
    const port = DESKTOP_BACKEND_PORT_START
      + ((offset + index) % DESKTOP_BACKEND_PORT_COUNT);
    const available = await new Promise<boolean>((resolve) => {
      const server = net.createServer();
      server.unref();
      server.once('error', () => resolve(false));
      server.listen({ host: '127.0.0.1', port }, () => {
        server.close(() => resolve(true));
      });
    });
    if (available) {
      return port;
    }
  }
  return null;
}

/**
 * 启动或恢复 Python 后端。
 * 安装版会记录进程归属，开发模式只等待外部后端就绪。
 */
async function ensurePythonBackend(): Promise<boolean> {
  if (backendStatus.state === 'ready') {
    return true;
  }
  if (backendStartupPromise) {
    return backendStartupPromise;
  }

  backendStartupPromise = (async () => {
    setBackendStatus({ state: 'starting', detail: '正在启动本地服务…' });

    if (isDev) {
      const ready = await waitForPythonBackend();
      setBackendStatus(ready
        ? { state: 'ready' }
        : { state: 'error', detail: '未检测到开发后端，请先启动 backend 服务。' });
      return ready;
    }

    await cleanupStalePythonBackend();
    const availablePort = await findAvailableBackendPort();
    if (!availablePort) {
      setBackendStatus({ state: 'error', detail: '未找到可用的本地服务端口，请重试。' });
      return false;
    }
    backendPort = availablePort;

    const backendProcess = resolvePackagedBackendProcess();
    if (!backendProcess) {
      setBackendStatus({ state: 'error', detail: '未找到内置服务，请重新安装应用。' });
      return false;
    }

    const userDataPath = app.getPath('userData');
    const dataPath = path.join(userDataPath, 'data');
    const logsPath = path.join(userDataPath, 'logs');
    fs.mkdirSync(dataPath, { recursive: true });
    fs.mkdirSync(logsPath, { recursive: true });

    backendInstanceId = randomUUID();
    appendDesktopLog('electron-main.log', `Starting backend from ${backendProcess.command}`);
    const env = {
      ...process.env,
      PYTHONUNBUFFERED: '1',
      AGENT_WORKSTATION_DESKTOP_MODE: '1',
      AGENT_WORKSTATION_DESKTOP_INSTANCE_ID: backendInstanceId,
      AGENT_WORKSTATION_DATA_DIR: userDataPath,
      AGENT_WORKSTATION_DATABASE_URL: `sqlite+aiosqlite:///${path.join(dataPath, 'agent_workstation.db')}`,
      AGENT_WORKSTATION_PORT: String(backendPort),
      AGENT_WORKSTATION_CORS_ORIGINS: '["http://localhost:5173","http://localhost:3000","null","file://"]',
    };

    pythonProcess = spawn(backendProcess.command, backendProcess.args, {
      cwd: backendProcess.cwd,
      env,
      stdio: ['pipe', 'pipe', 'pipe'],
      windowsHide: true,
    });

    if (!pythonProcess.pid) {
      setBackendStatus({ state: 'error', detail: '内置服务启动失败，请重试或重新安装应用。' });
      return false;
    }

    const pid = pythonProcess.pid;
    writeBackendOwner({
      pid,
      instanceId: backendInstanceId,
      startedAt: new Date().toISOString(),
      port: backendPort,
    });
    pythonProcess.stdout?.on('data', (data: Buffer) => {
      const output = data.toString().trim();
      console.log(`[Python] ${output}`);
      appendDesktopLog('python-backend.log', output);
    });
    pythonProcess.stderr?.on('data', (data: Buffer) => {
      const output = data.toString().trim();
      console.error(`[Python] ${output}`);
      appendDesktopLog('python-backend.log', `ERROR ${output}`);
    });
    let backendExited = false;
    let backendExitCode: number | null = null;
    pythonProcess.on('exit', (code: number | null) => {
      backendExited = true;
      backendExitCode = code;
      console.log(`[Python] Process exited with code ${code}`);
      appendDesktopLog('electron-main.log', `Backend process ${pid} exited with code ${code}`);
      removeBackendOwner(pid);
      if (pythonProcess?.pid === pid) {
        pythonProcess = null;
      }
      if (!appQuitting) {
        setBackendStatus({ state: 'error', detail: `本地服务已异常退出（代码 ${code ?? '未知'}）。` });
      }
    });

    const ready = await waitForPythonBackend(15000, () => backendExited);
    if (!ready) {
      await stopPythonBackend();
      if (!backendExited) {
        setBackendStatus({ state: 'error', detail: '本地服务启动超时，请重试或查看安装日志。' });
      } else {
        setBackendStatus({ state: 'error', detail: `本地服务启动失败（代码 ${backendExitCode ?? '未知'}）。` });
      }
      return false;
    }

    setBackendStatus({ state: 'ready' });
    return true;
  })().finally(() => {
    backendStartupPromise = null;
  });

  return backendStartupPromise;
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
        args: ['--host', '127.0.0.1', '--port', String(backendPort ?? 8765)],
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
      args: ['-m', 'uvicorn', 'src.server.main:app', '--host', '127.0.0.1', '--port', String(backendPort ?? 8765)],
      cwd: backendPath,
    };
  }

  const uvCommand = process.platform === 'win32' ? 'uv.cmd' : 'uv';
  return {
    command: uvCommand,
    args: ['run', 'python', '-m', 'uvicorn', 'src.server.main:app', '--host', '127.0.0.1', '--port', String(backendPort ?? 8765)],
    cwd: backendPath,
  };
}

/** 停止当前安装实例启动的 Python 后端及其子进程。 */
async function stopPythonBackend(): Promise<void> {
  const pid = pythonProcess?.pid ?? readBackendOwner()?.pid;
  if (!pid) {
    return;
  }
  await terminateProcessTree(pid);
  removeBackendOwner(pid);
  pythonProcess = null;
}

async function waitForPythonBackend(timeoutMs = 15000, shouldAbort?: () => boolean): Promise<boolean> {
  if (isDev) {
    return true;
  }
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (shouldAbort?.()) {
      return false;
    }
    try {
      const response = await fetch(`${backendServerUrl()}/api/health`, { cache: 'no-store' });
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
  appendDesktopLog('electron-main.log', `Application ready on ${process.platform}`);
  // Windows/Linux 默认会生成 File、Edit 等菜单。桌面工作台不提供这些菜单入口，
  // 显式移除以避免占用内容区域；macOS 仍保留系统菜单栏约定。
  if (process.platform !== 'darwin') {
    Menu.setApplicationMenu(null);
  }

  createMainWindow();
  // 先展示本地启动页，再异步等待后端，以免服务初始化期间出现空白窗口。
  void ensurePythonBackend();

  // macOS: 点击 Dock 图标时重新创建窗口
  app.on('activate', () => {
    if (mainWindow) {
      mainWindow.show();
      mainWindow.focus();
    } else if (BrowserWindow.getAllWindows().length === 0) {
      createMainWindow();
      void ensurePythonBackend();
    }
  });
});

// 所有窗口关闭时退出应用（macOS 除外）
app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

// 显式退出时等待进程树清理完成；macOS 关闭窗口本身不退出应用。
app.on('before-quit', (event) => {
  if (appQuitting) {
    return;
  }
  appQuitting = true;
  appendDesktopLog('electron-main.log', 'Application is quitting; stopping backend process.');
  event.preventDefault();
  void stopPythonBackend().finally(() => app.exit(0));
});

// ========== IPC Handlers ==========

/**
 * 获取 Python 后端服务地址
 * 前端通过此 IPC 获取 API base URL
 */
ipcMain.handle('get-backend-url', () => {
  return isDev ? DEV_BACKEND_SERVER_URL : backendServerUrl();
});

/** 获取内置后端状态，供启动页和运行期故障页使用。 */
ipcMain.handle('get-backend-status', () => backendStatus);

/** 用户触发恢复时，先结束旧实例，再重新启动后端。 */
ipcMain.handle('restart-backend', async () => {
  await stopPythonBackend();
  await ensurePythonBackend();
  return backendStatus;
});

/** 打开桌面端诊断日志目录，用于安装包启动失败后的排障。 */
ipcMain.handle('open-desktop-logs', async () => {
  const error = await shell.openPath(desktopLogsPath());
  return error ? { ok: false, error } : { ok: true };
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
