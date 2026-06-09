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
   * 获取平台信息
   * @returns 'darwin' | 'win32' | 'linux'
   */
  getPlatform: (): string => process.platform,
});
