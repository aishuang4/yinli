/**
 * Electron 预加载脚本
 *
 * 在渲染进程加载前执行，通过 contextBridge 安全地暴露 IPC 接口。
 * 渲染进程通过 window.electronAPI 访问这些方法，
 * 而不是直接使用 Node.js API（contextIsolation: true）。
 */

import { contextBridge, ipcRenderer } from 'electron';

/**
 * 暴露给渲染进程的 API
 * 渲染进程通过 window.electronAPI.xxx() 调用
 */
contextBridge.exposeInMainWorld('electronAPI', {
  /**
   * 获取 Python 后端服务地址
   * @returns 后端 URL，如 "http://localhost:8765"
   */
  getBackendUrl: (): Promise<string> => ipcRenderer.invoke('get-backend-url'),

  /**
   * 获取应用版本号
   * @returns 版本字符串，如 "0.1.0"
   */
  getAppVersion: (): Promise<string> => ipcRenderer.invoke('get-app-version'),

  /**
   * 选择 Project 工作目录，名称和绝对路径由 Electron 主进程返回
   */
  selectProjectDirectory: (): Promise<{ canceled: boolean; path?: string; name?: string }> => (
    ipcRenderer.invoke('select-project-directory')
  ),

  /**
   * 在系统文件管理器中打开一个本地文件夹或文件位置。
   * projectRoot 只作提示，主进程不会用它扩大白名单。
   */
  openPath: (targetPath: string, projectRoot?: string): Promise<{ ok: boolean; error?: string }> => (
    ipcRenderer.invoke('open-path', targetPath, projectRoot)
  ),

  /**
   * 打开安全白名单内的外部链接
   */
  openExternal: (url: string): Promise<{ ok: boolean; error?: string }> => (
    ipcRenderer.invoke('open-external', url)
  ),

  /**
   * 获取平台信息
   * @returns 'darwin' | 'win32' | 'linux'
   */
  getPlatform: (): string => process.platform,

  /** 获取内置后端当前生命周期状态。 */
  getBackendStatus: (): Promise<DesktopBackendStatus> => ipcRenderer.invoke('get-backend-status'),

  /** 重新启动内置后端，用于用户从启动故障页恢复。 */
  restartBackend: (): Promise<DesktopBackendStatus> => ipcRenderer.invoke('restart-backend'),

  /** 在系统文件管理器中打开桌面端诊断日志目录。 */
  openDesktopLogs: (): Promise<{ ok: boolean; error?: string }> => ipcRenderer.invoke('open-desktop-logs'),

  /** 订阅内置后端状态变化，返回取消订阅函数。 */
  onBackendStatus: (callback: (status: DesktopBackendStatus) => void): (() => void) => {
    const listener = (_event: Electron.IpcRendererEvent, status: DesktopBackendStatus) => callback(status);
    ipcRenderer.on('backend-status', listener);
    return () => ipcRenderer.removeListener('backend-status', listener);
  },
});

type DesktopBackendStatus = {
  state: 'starting' | 'ready' | 'error';
  detail?: string;
};
