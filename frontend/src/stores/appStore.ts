/**
 * 应用全局状态 Store — 基于 Zustand
 *
 * 管理当前视图、面板展开状态等 UI 状态。
 * 不持久化（每次打开应用重置）。
 */

import { create } from 'zustand';
import { isSurfaceEnabled, isViewEnabled } from './productFeatures';
import { useProductFeaturesStore } from './productFeaturesStore';

/** 应用视图类型（对应 Activity Bar 选项） */
export type ViewType = 'chat' | 'runs' | 'jobs' | 'traces' | 'sandbox' | 'agents' | 'subagents' | 'tools' | 'mcp' | 'skills' | 'memory' | 'settings';

/** 顶层产品工作空间模式 */
export type WorkspaceMode = 'workstation' | 'workflow';

/** Chat 之上的工作室表面，不是第三个 TitleBar 产品世界。 */
export type StudioSurface = 'none' | 'group-studio';

/** Chat ↔ 工作室过渡相位。idle 时才允许再次切换。 */
export type StudioTransition =
  | 'idle'
  | 'enter-collapse'
  | 'enter-expand'
  | 'enter-settle'
  | 'leave-collapse'
  | 'leave-expand'
  | 'leave-settle';

function prefersReducedMotion(): boolean {
  return typeof window !== 'undefined'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

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
  /** 工作室表面。不持久化，刷新回 Chat。 */
  studioSurface: StudioSurface;
  /** 过渡相位。非 idle 时两个 pane 同时可见。 */
  studioTransition: StudioTransition;
  /** 当前打开或刚刚离开的工作室 id。 */
  activeGroupStudioId: string | null;
  /** 未落库草稿的 Chat 会话绑定；持久化后清空。 */
  localStudioSeed: { chatSessionId: string; agentId: string; modelConfigId?: string } | null;
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
  /** 打开工作室表面。不改 TitleBar 的 WorkspaceMode 集合；若在任务流则先回到工作台 Chat。 */
  openGroupStudio: (groupId: string, seed?: { chatSessionId: string; agentId: string; modelConfigId?: string }) => void;
  /** 收起工作室，回到 Chat。 */
  closeGroupStudio: () => void;
  clearActiveGroupStudio: () => void;
  /** 由 App 壳层推进过渡相位。 */
  advanceStudioTransition: () => void;
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
  studioSurface: 'none',
  studioTransition: 'idle',
  activeGroupStudioId: null,
  localStudioSeed: null,

  // Actions
  setView: (view) => {
    const features = useProductFeaturesStore.getState().features;
    set({ currentView: isViewEnabled(features, view) ? view : 'chat' });
  },
  setWorkspaceMode: (mode) => {
    const features = useProductFeaturesStore.getState().features;
    if (mode === 'workflow' && !isSurfaceEnabled(features, 'workflow')) {
      set({ workspaceMode: 'workstation', currentView: 'chat' });
      return;
    }
    set({ workspaceMode: mode });
  },
  toggleDetailPanel: () =>
    set((state) => ({ detailPanelOpen: !state.detailPanelOpen })),
  setBackendConnected: (connected) => set({ backendConnected: connected }),
  setActiveContext: (activeWorkspaceId, activeProjectId) => set({
    activeWorkspaceId,
    activeProjectId,
  }),
  openScheduledJob: (jobId) => {
    const features = useProductFeaturesStore.getState().features;
    if (!isViewEnabled(features, 'jobs')) {
      return;
    }
    set({
      currentView: 'jobs',
      workspaceMode: 'workstation',
      pendingJobId: jobId,
    });
  },
  clearPendingJobId: () => set({ pendingJobId: null }),
  openGroupStudio: (groupId, seed) => set((state) => {
    if (!isSurfaceEnabled(useProductFeaturesStore.getState().features, 'groupStudio')) {
      return state;
    }
    const nextBase = {
      currentView: 'chat' as const,
      workspaceMode: 'workstation' as const,
      activeGroupStudioId: groupId,
      localStudioSeed: groupId.startsWith('gstu_local_')
        ? (seed ?? state.localStudioSeed)
        : null,
    };
    if (state.studioTransition !== 'idle') {
      return nextBase;
    }
    if (state.studioSurface === 'group-studio') {
      return { ...nextBase, studioSurface: 'group-studio' as const };
    }
    if (prefersReducedMotion()) {
      return { ...nextBase, studioSurface: 'group-studio' as const, studioTransition: 'idle' as const };
    }
    return { ...nextBase, studioTransition: 'enter-collapse' as const };
  }),
  closeGroupStudio: () => set((state) => {
    if (state.studioTransition !== 'idle' || state.studioSurface !== 'group-studio') {
      return state;
    }
    if (prefersReducedMotion()) {
      return { studioSurface: 'none', studioTransition: 'idle' };
    }
    return { studioTransition: 'leave-collapse' };
  }),
  clearActiveGroupStudio: () => set({ activeGroupStudioId: null, localStudioSeed: null }),
  advanceStudioTransition: () => set((state) => {
    if (state.studioTransition === 'enter-collapse') {
      return { studioTransition: 'enter-expand' };
    }
    if (state.studioTransition === 'enter-expand') {
      return { studioTransition: 'enter-settle' };
    }
    if (state.studioTransition === 'enter-settle') {
      return { studioTransition: 'idle', studioSurface: 'group-studio' };
    }
    if (state.studioTransition === 'leave-collapse') {
      return { studioTransition: 'leave-expand' };
    }
    if (state.studioTransition === 'leave-expand') {
      return { studioTransition: 'leave-settle' };
    }
    if (state.studioTransition === 'leave-settle') {
      return { studioTransition: 'idle', studioSurface: 'none' };
    }
    return state;
  }),
}));
