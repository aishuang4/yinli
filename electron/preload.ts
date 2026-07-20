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
   * 在系统文件管理器中打开一个本地文件夹或文件位置
   */
  openPath: (targetPath: string): Promise<{ ok: boolean; error?: string }> => (
    ipcRenderer.invoke('open-path', targetPath)
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
});
