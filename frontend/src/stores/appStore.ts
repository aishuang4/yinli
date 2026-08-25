/**
 * 应用全局状态 Store — 基于 Zustand
 *
 * 管理当前视图、面板展开状态等 UI 状态。
 * 不持久化（每次打开应用重置）。
 */

import { create } from 'zustand';

/** 应用视图类型（对应 Activity Bar 选项） */
export type ViewType = 'chat' | 'runs' | 'jobs' | 'traces' | 'sandbox' | 'agents' | 'subagents' | 'tools' | 'mcp' | 'skills' | 'memory' | 'settings';

/** 顶层产品工作空间模式 */
export type WorkspaceMode = 'workstation' | 'workflow';

/** 应用状态接口 */
interface AppState {
  /** 当前激活的视图 */
  currentView: ViewType;
  /** 当前顶层工作空间模式 */
  workspaceMode: WorkspaceMode;
  /** 详情面板是否展开 */
  detailPanelOpen: boolean;
  /** 后端连接状态 */
  backendConnected: boolean;
  /** 当前资源隔离 Workspace。 */
  activeWorkspaceId: string;
  /** 当前会话和运行所属 Project。 */
  activeProjectId: string;
  /** 通知入口要打开的定时任务，消费后清空。 */
  pendingJobId: string | null;

  // Actions
  /** 切换视图 */
  setView: (view: ViewType) => void;
  /** 切换顶层工作空间模式 */
  setWorkspaceMode: (mode: WorkspaceMode) => void;
  /** 切换详情面板 */
  toggleDetailPanel: () => void;
  /** 设置后端连接状态 */
  setBackendConnected: (connected: boolean) => void;
  /** 原子切换 Workspace / Project，避免出现不一致的中间状态。 */
  setActiveContext: (workspaceId: string, projectId: string) => void;
  /** 从通知跳转到指定定时任务。 */
  openScheduledJob: (jobId: string) => void;
  /** 消费通知跳转后的 pending Job。 */
  clearPendingJobId: () => void;
}

export const useAppStore = create<AppState>()((set) => ({
  // 初始状态
  currentView: 'chat',
  workspaceMode: 'workstation',
  detailPanelOpen: false,
  backendConnected: false,
  activeWorkspaceId: 'default',
  activeProjectId: 'default',
  pendingJobId: null,

  // Actions
  setView: (view) => set({ currentView: view }),
  setWorkspaceMode: (mode) => set({ workspaceMode: mode }),
  toggleDetailPanel: () =>
    set((state) => ({ detailPanelOpen: !state.detailPanelOpen })),
  setBackendConnected: (connected) => set({ backendConnected: connected }),
  setActiveContext: (activeWorkspaceId, activeProjectId) => set({
    activeWorkspaceId,
    activeProjectId,
  }),
  openScheduledJob: (jobId) => set({
    currentView: 'jobs',
    workspaceMode: 'workstation',
    pendingJobId: jobId,
  }),
  clearPendingJobId: () => set({ pendingJobId: null }),
}));
