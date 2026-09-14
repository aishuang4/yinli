/**
 * 全局设置 Store — 基于 Zustand
 *
 * 管理主题、语言、后端连接等全局设置。
 * 持久化到 localStorage。
 */

import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { ThemeName } from '../types/theme';

/** 设置状态接口 */
interface SettingsState {
  /** 当前主题 */
  theme: ThemeName;
  /** 后端 API 地址 */
  backendUrl: string;
  /** 应用版本号 */
  appVersion: string;

  // Actions
  /** 设置主题 */
  setTheme: (theme: ThemeName) => void;
  /** 切换亮/暗主题 */
  toggleTheme: () => void;
  /** 设置后端地址 */
  setBackendUrl: (url: string) => void;
  /** 设置版本号 */
  setAppVersion: (version: string) => void;
}

export const useSettingsStore = create<SettingsState>()(
  persist(
    (set) => ({
      // 初始状态
      theme: 'light',
      backendUrl: 'http://localhost:8765',
      appVersion: '0.1.0',

      // Actions
      setTheme: (theme) => set({ theme }),

      toggleTheme: () =>
        set((state) => ({
          theme: state.theme === 'light' ? 'dark' : 'light',
        })),

      setBackendUrl: (url) => set({ backendUrl: url }),
      setAppVersion: (version) => set({ appVersion: version }),
    }),
    {
      name: 'agent-workstation-settings',
      version: 2,
      // 打包后端端口每次启动都会变，不能把 backendUrl 写进 localStorage。
      partialize: (state) => ({
        theme: state.theme,
      }),
      migrate: (persisted) => {
        const data = persisted as { theme?: 'light' | 'dark' };
        return { theme: data.theme === 'dark' ? 'dark' : 'light' };
      },
    }
  )
);
