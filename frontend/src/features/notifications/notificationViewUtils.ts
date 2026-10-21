/**
 * 通知展示用的图标、时间和未读文案。
 */

import { CalendarClock, CheckCircle2, CircleAlert, Clock3, PauseCircle, Users } from 'lucide-react';
import type { DesktopNotificationPayload, UserNotification } from '../../types/notification';
import { useAppStore } from '../../stores/appStore';

export const NOTIFICATION_KIND_ICONS: Record<string, typeof CheckCircle2> = {
  job_completed: CheckCircle2,
  job_failed: CircleAlert,
  job_missed: Clock3,
  job_paused: PauseCircle,
  group_studio_circuit: PauseCircle,
  group_studio_awaiting_human: Users,
  group_studio_completed: CheckCircle2,
  group_studio_failed: CircleAlert,
};

export const NOTIFICATION_KIND_LABELS: Record<string, string> = {
  job_completed: '已完成',
  job_failed: '执行失败',
  job_missed: '已错过',
  job_paused: '已暂停',
  group_studio_circuit: '工作室熔断',
  group_studio_awaiting_human: '等待确认',
  group_studio_completed: '工作室完成',
  group_studio_failed: '工作室失败',
};

export function isGroupStudioNotification(kind: string): boolean {
  return kind.startsWith('group_studio_');
}

export function routeNotificationClick(
  item: UserNotification,
  openInbox: (item: UserNotification) => void,
): void {
  if (isGroupStudioNotification(item.kind)) {
    const groupId = String(item.payload?.groupStudioId || '');
    if (groupId) {
      useAppStore.getState().openGroupStudio(groupId);
      return;
    }
  }
  openInbox(item);
}

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

export function notificationFromDesktopPayload(payload: DesktopNotificationPayload): UserNotification {
  return {
    id: payload.id,
    projectId: '',
    jobId: payload.jobId,
    firingId: payload.firingId,
    kind: payload.kind,
    title: payload.title,
    body: payload.body,
    payload: payload.groupStudioId ? { groupStudioId: payload.groupStudioId } : {},
    readAt: null,
    createdAt: new Date().toISOString(),
  };
}
