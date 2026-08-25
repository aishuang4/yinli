/**
 * NotificationToastHost — 定时任务完成时的即时提醒。
 *
 * Web：右侧应用内提醒框。Electron：主进程系统通知，失败时回退到提醒框。
 * 同时持有全局 SSE，避免只在 Chat 铃铛挂载时才能收到完成事件。
 */

import { useEffect } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';
import { NotificationResultModal } from './NotificationResultModal';
import {
  formatNotificationTime,
  notificationFromDesktopPayload,
  notificationKindIcon,
} from './notificationViewUtils';
import { useNotifications } from '../../hooks/useNotifications';
import { useAppStore } from '../../stores/appStore';
import { useNotificationStore } from '../../stores/notificationStore';
import type { DesktopNotificationPayload, UserNotification } from '../../types/notification';
import { getErrorMessage, notifyError } from '../../utils/feedback';
import './NotificationToastHost.css';

const TOAST_DURATION_MS = 8_000;

export function NotificationToastHost() {
  const { markRead } = useNotifications({ subscribe: true });
  const toasts = useNotificationStore((state) => state.toasts);
  const selected = useNotificationStore((state) => state.selected);
  const openNotification = useNotificationStore((state) => state.openNotification);
  const closeResult = useNotificationStore((state) => state.closeResult);
  const dismissToast = useNotificationStore((state) => state.dismissToast);
  const openScheduledJob = useAppStore((state) => state.openScheduledJob);

  useEffect(() => {
    const subscribe = window.electronAPI?.onNotificationClicked;
    if (!subscribe) return undefined;
    return subscribe((payload: DesktopNotificationPayload) => {
      const existing = useNotificationStore.getState().items.find((item) => item.id === payload.id);
      const item = existing || notificationFromDesktopPayload(payload);
      openNotification(item);
      if (!item.readAt) {
        void markRead(item.id).catch(() => {
          // 系统通知点击后标已读失败不阻断打开结果。
        });
      }
    });
  }, [markRead, openNotification]);

  const handleOpen = async (item: UserNotification) => {
    openNotification(item);
    if (item.readAt) return;
    try {
      await markRead(item.id);
    } catch (err) {
      notifyError(getErrorMessage(err, '标记已读失败'));
    }
  };

  return (
    <>
      {toasts.length > 0 ? createPortal((
        <div className="notification-toasts" aria-live="polite">
          {toasts.map((item) => (
            <NotificationToast
              key={item.id}
              item={item}
              onOpen={() => void handleOpen(item)}
              onDismiss={() => dismissToast(item.id)}
            />
          ))}
        </div>
      ), document.body) : null}
      <NotificationResultModal
        notification={selected}
        onClose={closeResult}
        onOpenJob={(jobId) => {
          closeResult();
          openScheduledJob(jobId);
        }}
      />
    </>
  );
}

function NotificationToast({
  item,
  onOpen,
  onDismiss,
}: {
  item: UserNotification;
  onOpen: () => void;
  onDismiss: () => void;
}) {
  const Icon = notificationKindIcon(item.kind);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      useNotificationStore.getState().dismissToast(item.id);
    }, TOAST_DURATION_MS);
    return () => window.clearTimeout(timer);
  }, [item.id]);

  return (
    <div className="notification-toast" role="status">
      <button
        type="button"
        className="notification-toast__main"
        onClick={onOpen}
      >
        <span className="notification-toast__icon">
          <Icon size={16} strokeWidth={1.8} />
        </span>
        <span className="notification-toast__copy">
          <span className="notification-toast__title">{item.title}</span>
          {item.body ? <span className="notification-toast__body">{item.body}</span> : null}
          <span className="notification-toast__meta">{formatNotificationTime(item.createdAt)}</span>
        </span>
      </button>
      <button
        type="button"
        className="notification-toast__close"
        aria-label="关闭提醒"
        onClick={onDismiss}
      >
        <X size={14} />
      </button>
    </div>
  );
}
