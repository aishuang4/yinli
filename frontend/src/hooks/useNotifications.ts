/**
 * useNotifications — 应用内通知 inbox 与独立 SSE。
 *
 * 未读数走 `/api/notifications/stream`，断开后回退 15s 轮询。
 * 不订阅 Chat SSE。
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { useAppStore } from '../stores/appStore';
import type {
  NotificationListResponse,
  NotificationStreamEvent,
  UserNotification,
} from '../types/notification';
import { resolveApiUrl } from '../utils/apiUrl';
import { getErrorMessage } from '../utils/feedback';

const POLL_INTERVAL_MS = 15_000;

interface UseNotificationsReturn {
  items: UserNotification[];
  unreadCount: number;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  markRead: (notificationId: string) => Promise<UserNotification>;
  markAllRead: () => Promise<void>;
}

function mergeNotification(items: UserNotification[], next: UserNotification): UserNotification[] {
  const remaining = items.filter((item) => item.id !== next.id);
  return [next, ...remaining];
}

export function useNotifications(): UseNotificationsReturn {
  const activeProjectId = useAppStore((state) => state.activeProjectId);
  const [items, setItems] = useState<UserNotification[]>([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const hydratedRef = useRef(false);
  const projectQuery = activeProjectId ? `projectId=${encodeURIComponent(activeProjectId)}` : '';

  const applyStreamEvent = useCallback((event: NotificationStreamEvent) => {
    if (typeof event.unreadCount === 'number') {
      setUnreadCount(event.unreadCount);
    }
    if (event.type === 'notification' && event.notification && hydratedRef.current) {
      setItems((current) => mergeNotification(current, event.notification as UserNotification));
    }
    if (event.type === 'read' && event.notification && hydratedRef.current) {
      setItems((current) => current.map((item) => (
        item.id === event.notification?.id ? event.notification as UserNotification : item
      )));
    }
    if (event.type === 'read_all' && hydratedRef.current) {
      setItems((current) => current.map((item) => (
        item.readAt ? item : { ...item, readAt: new Date().toISOString() }
      )));
    }
  }, []);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const query = projectQuery ? `?${projectQuery}` : '';
      const response = await fetch(`/api/notifications${query}`);
      if (!response.ok) {
        throw new Error(await response.text());
      }
      const payload = await response.json() as NotificationListResponse;
      setItems(payload.items);
      setUnreadCount(payload.unreadCount);
      hydratedRef.current = true;
    } catch (err) {
      setError(getErrorMessage(err, '加载通知失败'));
    } finally {
      setLoading(false);
    }
  }, [projectQuery]);

  const syncUnreadCount = useCallback(async () => {
    const query = projectQuery ? `?${projectQuery}` : '';
    const response = await fetch(`/api/notifications/unread-count${query}`);
    if (!response.ok) return;
    const payload = await response.json() as { unreadCount?: number };
    if (typeof payload.unreadCount === 'number') {
      setUnreadCount(payload.unreadCount);
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
    setItems((current) => current.map((item) => (item.id === record.id ? record : item)));
    await syncUnreadCount();
    return record;
  }, [syncUnreadCount]);

  const markAllRead = useCallback(async () => {
    const query = projectQuery ? `?${projectQuery}` : '';
    const response = await fetch(`/api/notifications/read-all${query}`, { method: 'POST' });
    if (!response.ok) {
      throw new Error(await response.text());
    }
    setItems((current) => current.map((item) => (
      item.readAt ? item : { ...item, readAt: new Date().toISOString() }
    )));
    setUnreadCount(0);
  }, [projectQuery]);

  useEffect(() => {
    hydratedRef.current = false;
    setItems([]);
    setUnreadCount(0);
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
          setUnreadCount(payload.unreadCount);
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
  }, [applyStreamEvent, projectQuery]);

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
