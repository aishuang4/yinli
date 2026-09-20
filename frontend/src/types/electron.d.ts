import type { DesktopBackendStatus } from './desktopBackend';
import type { DesktopNotificationPayload } from './notification';

/**
 * Electron IPC API 类型声明
 *
 * 声明 window.electronAPI 接口，供渲染进程类型安全地调用主进程方法。
 */

interface ElectronAPI {
  /** 获取 Python 后端服务地址 */
  getBackendUrl(): Promise<string>;
  /** 获取应用版本号 */
  getAppVersion(): Promise<string>;
  /** 选择 Project 工作目录 */
  selectProjectDirectory(): Promise<{ canceled: boolean; path?: string; name?: string }>;
  /** 在系统文件管理器中打开本地路径。projectRoot 仅作提示，主进程不据此扩大白名单。 */
  openPath(targetPath: string, projectRoot?: string): Promise<{ ok: boolean; error?: string }>;
  /** 把系统文件预览嵌进当前窗口矩形。 */
  embedFilePreview(payload: {
    path: string;
    x: number;
    y: number;
    width: number;
    height: number;
  }): Promise<{ ok: boolean; error?: string }>;
  updateEmbeddedPreview(payload: {
    x: number;
    y: number;
    width: number;
    height: number;
  }): Promise<{ ok: boolean }>;
  closeEmbeddedPreview(): Promise<{ ok: boolean }>;
  /** 打开安全白名单内的外部链接 */
  openExternal(url: string): Promise<{ ok: boolean; error?: string }>;
  /** 获取当前平台 */
  getPlatform(): string;
  /** 获取内置后端状态 */
  getBackendStatus(): Promise<DesktopBackendStatus>;
  /** 重新启动内置后端 */
  restartBackend(): Promise<DesktopBackendStatus>;
  /** 打开桌面端诊断日志目录 */
  openDesktopLogs(): Promise<{ ok: boolean; error?: string }>;
  /** 获取桌面端实际用户数据目录 */
  getUserDataPath(): Promise<string>;
  /** 订阅内置后端状态变化 */
  onBackendStatus(callback: (status: DesktopBackendStatus) => void): () => void;
  /** 弹出操作系统通知。 */
  showNotification(payload: DesktopNotificationPayload): Promise<{ ok: boolean; error?: string }>;
  /** 用户点击系统通知时回调，返回取消订阅函数。 */
  onNotificationClicked(callback: (payload: DesktopNotificationPayload) => void): () => void;
}

declare global {
  interface Window {
    /** Electron 预加载脚本暴露的 API（仅在 Electron 环境可用） */
    electronAPI?: ElectronAPI;
  }
}

export {};
