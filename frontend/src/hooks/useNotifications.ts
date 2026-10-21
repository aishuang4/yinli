/**
 * useNotifications — 应用内通知 inbox 与独立 SSE。
 *
 * 未读数走 `/api/notifications/stream`，断开后回退 15s 轮询。
 * 不订阅 Chat SSE。
 * 全局只允许 NotificationLiveHost 以 subscribe: true 建立一条 SSE。
 */

import { useCallback, useEffect } from 'react';
import { useNotificationStore } from '../stores/notificationStore';
import { useAppStore } from '../stores/appStore';
import type {
  DesktopNotificationPayload,
  NotificationListResponse,
  NotificationStreamEvent,
  UserNotification,
} from '../types/notification';
import { resolveApiUrl } from '../utils/apiUrl';
import { getErrorMessage } from '../utils/feedback';

const POLL_INTERVAL_MS = 15_000;

interface UseNotificationsOptions {
  /** 为 true 时建立 SSE；全局只应有一处订阅。 */
  subscribe?: boolean;
}

interface UseNotificationsReturn {
  items: UserNotification[];
  unreadCount: number;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  markRead: (notificationId: string) => Promise<UserNotification>;
  markAllRead: () => Promise<void>;
}

function toDesktopPayload(item: UserNotification): DesktopNotificationPayload {
  return {
    id: item.id,
    title: item.title,
    body: item.body,
    kind: item.kind,
    jobId: item.jobId,
    firingId: item.firingId,
    groupStudioId: String(item.payload?.groupStudioId || ''),
  };
}

function presentIncomingNotification(item: UserNotification) {
  const desktop = window.electronAPI?.showNotification;
  if (desktop) {
    void desktop(toDesktopPayload(item)).then((result) => {
      if (!result?.ok) {
        useNotificationStore.getState().pushToast(item);
      }
    }).catch(() => {
      useNotificationStore.getState().pushToast(item);
    });
    return;
  }
  useNotificationStore.getState().pushToast(item);
}

export function useNotifications(options: UseNotificationsOptions = {}): UseNotificationsReturn {
  const subscribe = Boolean(options.subscribe);
  const activeProjectSlug = useAppStore((state) => state.activeProjectSlug);
  const items = useNotificationStore((state) => state.items);
  const unreadCount = useNotificationStore((state) => state.unreadCount);
  const loading = useNotificationStore((state) => state.loading);
  const error = useNotificationStore((state) => state.error);
  const projectQuery = activeProjectSlug ? `projectSlug=${encodeURIComponent(activeProjectSlug)}` : '';

  const refresh = useCallback(async () => {
    useNotificationStore.getState().setInbox({ loading: true, error: null });
    try {
      const query = projectQuery ? `?${projectQuery}` : '';
      const response = await fetch(`/api/notifications${query}`);
      if (!response.ok) {
        throw new Error(await response.text());
      }
      const payload = await response.json() as NotificationListResponse;
      useNotificationStore.getState().setInbox({
        items: payload.items,
        unreadCount: payload.unreadCount,
        loading: false,
        error: null,
      });
    } catch (err) {
      useNotificationStore.getState().setInbox({
        loading: false,
        error: getErrorMessage(err, '加载通知失败'),
      });
    }
  }, [projectQuery]);

  const syncUnreadCount = useCallback(async () => {
    const query = projectQuery ? `?${projectQuery}` : '';
    const response = await fetch(`/api/notifications/unread-count${query}`);
    if (!response.ok) return;
    const payload = await response.json() as { unreadCount?: number };
    if (typeof payload.unreadCount === 'number') {
      useNotificationStore.getState().setInbox({ unreadCount: payload.unreadCount });
    }
  }, [projectQuery]);

  const markRead = useCallback(async (notificationId: string) => {
    const response = await fetch(`/api/notifications/${encodeURIComponent(notificationId)}/read`, {
      method: 'POST',
    });
    if (!response.ok) {
      throw new Error(await response.text());
    }
    const record = await response.json() as UserNotification;
    useNotificationStore.getState().mergeItem(record);
    await syncUnreadCount();
    return record;
  }, [syncUnreadCount]);

  const markAllRead = useCallback(async () => {
    const query = projectQuery ? `?${projectQuery}` : '';
    const response = await fetch(`/api/notifications/read-all${query}`, { method: 'POST' });
    if (!response.ok) {
      throw new Error(await response.text());
    }
    useNotificationStore.getState().markItemsRead();
    useNotificationStore.getState().setInbox({ unreadCount: 0 });
  }, [projectQuery]);

  useEffect(() => {
    if (!subscribe) return undefined;
    useNotificationStore.getState().reset();
    let closed = false;
    let source: EventSource | null = null;
    let pollTimer: number | null = null;
    let retryTimer: number | null = null;

    const stopPoll = () => {
      if (pollTimer !== null) {
        window.clearInterval(pollTimer);
        pollTimer = null;
      }
    };

    const pollUnread = async () => {
      try {
        const query = projectQuery ? `?${projectQuery}` : '';
        const response = await fetch(`/api/notifications/unread-count${query}`);
        if (!response.ok) return;
        const payload = await response.json() as { unreadCount?: number };
        if (!closed && typeof payload.unreadCount === 'number') {
          useNotificationStore.getState().setInbox({ unreadCount: payload.unreadCount });
        }
      } catch {
        // 轮询失败时保留上次未读数，等待下次重试。
      }
    };

    const startPoll = () => {
      if (pollTimer !== null) return;
      void pollUnread();
      pollTimer = window.setInterval(() => {
        void pollUnread();
      }, POLL_INTERVAL_MS);
    };

    const applyStreamEvent = (event: NotificationStreamEvent) => {
      const store = useNotificationStore.getState();
      if (typeof event.unreadCount === 'number') {
        store.setInbox({ unreadCount: event.unreadCount });
      }
      if (event.type === 'notification' && event.notification) {
        store.mergeItem(event.notification);
        presentIncomingNotification(event.notification);
      }
      if (event.type === 'read' && event.notification) {
        store.mergeItem(event.notification);
      }
      if (event.type === 'read_all') {
        store.markItemsRead();
      }
    };

    const handleStreamEvent = (event: MessageEvent<string>) => {
      try {
        applyStreamEvent(JSON.parse(event.data) as NotificationStreamEvent);
      } catch {
        // 忽略心跳或无法解析的帧。
      }
    };

    const connect = () => {
      if (closed) return;
      const query = projectQuery ? `?${projectQuery}` : '';
      source = new EventSource(resolveApiUrl(`/api/notifications/stream${query}`));
      ['snapshot', 'notification', 'read', 'read_all'].forEach((eventName) => {
        source?.addEventListener(eventName, handleStreamEvent as EventListener);
      });
      source.onopen = () => {
        stopPoll();
      };
      source.onerror = () => {
        source?.close();
        source = null;
        startPoll();
        if (retryTimer !== null) window.clearTimeout(retryTimer);
        retryTimer = window.setTimeout(connect, POLL_INTERVAL_MS);
      };
    };

    void pollUnread();
    connect();

    return () => {
      closed = true;
      source?.close();
      stopPoll();
      if (retryTimer !== null) window.clearTimeout(retryTimer);
    };
  }, [projectQuery, subscribe]);

  return {
    items,
    unreadCount,
    loading,
    error,
    refresh,
    markRead,
    markAllRead,
  };
}
