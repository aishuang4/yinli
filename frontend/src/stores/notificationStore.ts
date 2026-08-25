/**
 * 通知展示状态 — inbox、右侧提醒框和结果弹窗。
 *
 * SSE 连接由 useNotifications({ subscribe: true }) 唯一持有。
 */

import { create } from 'zustand';
import type { UserNotification } from '../types/notification';

const MAX_TOASTS = 3;

interface NotificationState {
  items: UserNotification[];
  unreadCount: number;
  loading: boolean;
  error: string | null;
  toasts: UserNotification[];
  selected: UserNotification | null;
  setInbox: (payload: { items?: UserNotification[]; unreadCount?: number; loading?: boolean; error?: string | null }) => void;
  mergeItem: (item: UserNotification) => void;
  markItemsRead: (notificationId?: string) => void;
  pushToast: (item: UserNotification) => void;
  dismissToast: (id: string) => void;
  openNotification: (item: UserNotification) => void;
  closeResult: () => void;
  reset: () => void;
}

function mergeNotification(items: UserNotification[], next: UserNotification): UserNotification[] {
  return [next, ...items.filter((item) => item.id !== next.id)];
}

export const useNotificationStore = create<NotificationState>()((set) => ({
  items: [],
  unreadCount: 0,
  loading: false,
  error: null,
  toasts: [],
  selected: null,

  setInbox: (payload) => set((state) => ({
    items: payload.items ?? state.items,
    unreadCount: payload.unreadCount ?? state.unreadCount,
    loading: payload.loading ?? state.loading,
    error: payload.error === undefined ? state.error : payload.error,
  })),

  mergeItem: (item) => set((state) => ({
    items: mergeNotification(state.items, item),
  })),

  markItemsRead: (notificationId) => set((state) => ({
    items: state.items.map((item) => {
      if (item.readAt) return item;
      if (notificationId && item.id !== notificationId) return item;
      return { ...item, readAt: new Date().toISOString() };
    }),
  })),

  pushToast: (item) => set((state) => ({
    toasts: [item, ...state.toasts.filter((toast) => toast.id !== item.id)].slice(0, MAX_TOASTS),
  })),

  dismissToast: (id) => set((state) => ({
    toasts: state.toasts.filter((toast) => toast.id !== id),
  })),

  openNotification: (item) => set((state) => ({
    selected: item,
    toasts: state.toasts.filter((toast) => toast.id !== item.id),
  })),

  closeResult: () => set({ selected: null }),

  reset: () => set({
    items: [],
    unreadCount: 0,
    loading: false,
    error: null,
    toasts: [],
    selected: null,
  }),
}));
