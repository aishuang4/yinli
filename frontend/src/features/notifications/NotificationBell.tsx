/**
 * NotificationBell — Chat 顶栏设置按钮左侧的任务通知入口。
 */

import { useEffect, useRef, useState, type CSSProperties } from 'react';
import { createPortal } from 'react-dom';
import { Bell, CalendarClock, CheckCircle2, CircleAlert, Clock3, PauseCircle } from 'lucide-react';
import { Tooltip } from '@/components/design';
import { NotificationResultModal } from './NotificationResultModal';
import { useNotifications } from '../../hooks/useNotifications';
import { useAppStore } from '../../stores/appStore';
import type { UserNotification } from '../../types/notification';
import { getErrorMessage, notifyError } from '../../utils/feedback';
import './NotificationBell.css';

const KIND_ICONS: Record<string, typeof CheckCircle2> = {
  job_completed: CheckCircle2,
  job_failed: CircleAlert,
  job_missed: Clock3,
  job_paused: PauseCircle,
};

function formatNotificationTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  const diff = Date.now() - date.getTime();
  if (diff < 60_000) return '刚刚';
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前`;
  return date.toLocaleString('zh-CN', {
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function unreadLabel(count: number): string {
  if (count <= 0) return '';
  return count > 99 ? '99+' : String(count);
}

export function NotificationBell() {
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<UserNotification | null>(null);
  const [panelStyle, setPanelStyle] = useState<CSSProperties>({});
  const rootRef = useRef<HTMLDivElement | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const setView = useAppStore((state) => state.setView);
  const openScheduledJob = useAppStore((state) => state.openScheduledJob);
  const {
    items,
    unreadCount,
    loading,
    error,
    refresh,
    markRead,
    markAllRead,
  } = useNotifications();

  useEffect(() => {
    if (!open) return undefined;
    void refresh();
    const trigger = rootRef.current?.querySelector('button');
    if (trigger) {
      const rect = trigger.getBoundingClientRect();
      setPanelStyle({
        top: rect.bottom + 8,
        right: Math.max(12, window.innerWidth - rect.right),
      });
    }
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node;
      if (rootRef.current?.contains(target) || panelRef.current?.contains(target)) {
        return;
      }
      setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !selected) setOpen(false);
    };
    window.addEventListener('pointerdown', onPointerDown);
    window.addEventListener('keydown', onKeyDown);
    return () => {
      window.removeEventListener('pointerdown', onPointerDown);
      window.removeEventListener('keydown', onKeyDown);
    };
  }, [open, refresh, selected]);

  const handleToggle = () => {
    setOpen((current) => !current);
  };

  const handleItemClick = async (item: UserNotification) => {
    try {
      if (!item.readAt) {
        await markRead(item.id);
      }
    } catch (err) {
      notifyError(getErrorMessage(err, '标记已读失败'));
    }
    setOpen(false);
    setSelected(item);
  };

  const handleMarkAll = async () => {
    try {
      await markAllRead();
    } catch (err) {
      notifyError(getErrorMessage(err, '全部标为已读失败'));
    }
  };

  const badge = unreadLabel(unreadCount);

  return (
    <div className="notification-bell" ref={rootRef}>
      <Tooltip title="任务通知" placement="bottom" mode="dark">
        <button
          type="button"
          className="icon-button chat-workbench-top-action notification-bell__trigger h-9 w-9 rounded-xl"
          onClick={handleToggle}
          aria-label={badge ? `任务通知，${unreadCount} 条未读` : '任务通知'}
          aria-expanded={open}
        >
          <Bell size={17} />
          {badge ? <span className="notification-bell__badge">{badge}</span> : null}
        </button>
      </Tooltip>
      {open && createPortal((
        <div
          ref={panelRef}
          className="notification-bell__panel"
          style={panelStyle}
          role="dialog"
          aria-label="任务通知"
        >
          <div className="notification-bell__header">
            <div className="notification-bell__title">任务通知</div>
            <button
              type="button"
              className="notification-bell__mark-all"
              onClick={() => void handleMarkAll()}
              disabled={unreadCount === 0}
            >
              全部已读
            </button>
          </div>
          <div className="notification-bell__list">
            {loading && items.length === 0 ? (
              <div className="notification-bell__status">正在加载通知…</div>
            ) : error && items.length === 0 ? (
              <div className="notification-bell__status">{error}</div>
            ) : items.length === 0 ? (
              <div className="notification-bell__empty">暂无定时任务通知</div>
            ) : (
              items.map((item) => {
                const Icon = KIND_ICONS[item.kind] || CalendarClock;
                const unread = !item.readAt;
                return (
                  <button
                    key={item.id}
                    type="button"
                    className={`notification-bell__item${unread ? ' notification-bell__item--unread' : ''}`}
                    onClick={() => void handleItemClick(item)}
                  >
                    <span className="notification-bell__icon">
                      <Icon size={15} strokeWidth={1.8} />
                    </span>
                    <span className="notification-bell__copy">
                      <span className="notification-bell__item-title">
                        {unread ? <span className="notification-bell__dot" /> : null}
                        <span className="truncate">{item.title}</span>
                      </span>
                      {item.body ? (
                        <span className="notification-bell__body">{item.body}</span>
                      ) : null}
                      <span className="notification-bell__meta">
                        {formatNotificationTime(item.createdAt)}
                      </span>
                    </span>
                  </button>
                );
              })
            )}
          </div>
          <div className="notification-bell__footer">
            <button
              type="button"
              className="notification-bell__footer-link"
              onClick={() => {
                setOpen(false);
                setView('jobs');
              }}
            >
              打开定时任务
            </button>
          </div>
        </div>
      ), document.body)}
      <NotificationResultModal
        notification={selected}
        onClose={() => setSelected(null)}
        onOpenJob={(jobId) => {
          setSelected(null);
          openScheduledJob(jobId);
        }}
      />
    </div>
  );
}
