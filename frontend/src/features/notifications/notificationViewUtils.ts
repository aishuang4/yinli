/**
 * 通知展示用的图标、时间和未读文案。
 */

import { CalendarClock, CheckCircle2, CircleAlert, Clock3, PauseCircle } from 'lucide-react';
import type { UserNotification } from '../../types/notification';

export const NOTIFICATION_KIND_ICONS: Record<string, typeof CheckCircle2> = {
  job_completed: CheckCircle2,
  job_failed: CircleAlert,
  job_missed: Clock3,
  job_paused: PauseCircle,
};

export const NOTIFICATION_KIND_LABELS: Record<string, string> = {
  job_completed: '已完成',
  job_failed: '执行失败',
  job_missed: '已错过',
  job_paused: '已暂停',
};

export function notificationKindIcon(kind: string) {
  return NOTIFICATION_KIND_ICONS[kind] || CalendarClock;
}

export function formatNotificationTime(value: string): string {
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

export function unreadLabel(count: number): string {
  if (count <= 0) return '';
  return count > 99 ? '99+' : String(count);
}

export function notificationFromDesktopPayload(payload: {
  id: string;
  title: string;
  body: string;
  kind: string;
  jobId: string;
  firingId: string;
}): UserNotification {
  return {
    id: payload.id,
    workspaceId: '',
    projectId: '',
    jobId: payload.jobId,
    firingId: payload.firingId,
    kind: payload.kind,
    title: payload.title,
    body: payload.body,
    payload: {},
    readAt: null,
    createdAt: new Date().toISOString(),
  };
}
