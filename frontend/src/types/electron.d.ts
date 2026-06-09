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
  /** 获取当前平台 */
  getPlatform(): string;
}

declare global {
  interface Window {
    /** Electron 预加载脚本暴露的 API（仅在 Electron 环境可用） */
    electronAPI?: ElectronAPI;
  }
}

export {};
