/**
 * 应用全局状态 Store — 基于 Zustand
 *
 * 管理当前视图、面板展开状态等 UI 状态。
 * 不持久化（每次打开应用重置）。
 */

import { create } from 'zustand';

/** 应用视图类型（对应 Activity Bar 选项） */
export type ViewType = 'chat' | 'runs' | 'traces' | 'sandbox' | 'agents' | 'mcp' | 'skills' | 'settings';

/** 应用状态接口 */
interface AppState {
  /** 当前激活的视图 */
  currentView: ViewType;
  /** 详情面板是否展开 */
  detailPanelOpen: boolean;
  /** 后端连接状态 */
  backendConnected: boolean;

  // Actions
  /** 切换视图 */
  setView: (view: ViewType) => void;
  /** 切换详情面板 */
  toggleDetailPanel: () => void;
  /** 设置后端连接状态 */
  setBackendConnected: (connected: boolean) => void;
}

export const useAppStore = create<AppState>()((set) => ({
  // 初始状态
  currentView: 'chat',
  detailPanelOpen: false,
  backendConnected: false,

  // Actions
  setView: (view) => set({ currentView: view }),
  toggleDetailPanel: () =>
    set((state) => ({ detailPanelOpen: !state.detailPanelOpen })),
  setBackendConnected: (connected) => set({ backendConnected: connected }),
}));
