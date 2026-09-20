/**
 * Subagent 管理页展示工具函数。
 */

import type { SubagentDeliveryStatus, SubagentInvocationStatus } from '../../types/subagent';

export function formatDateTime(value?: string | null): string {
  if (!value) return '未完成';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

export function formatDuration(startedAt?: string, completedAt?: string | null): string {
  if (!startedAt || !completedAt) return '';
  const start = new Date(startedAt).getTime();
  const end = new Date(completedAt).getTime();
  if (Number.isNaN(start) || Number.isNaN(end) || end < start) return '';
  const seconds = Math.max(1, Math.round((end - start) / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return rest > 0 ? `${minutes}m ${rest}s` : `${minutes}m`;
}

export function invocationStatusColor(status: SubagentInvocationStatus): string {
  if (status === 'completed') return 'green';
  if (status === 'failed') return 'red';
  if (status === 'running') return 'blue';
  if (status === 'queued') return 'blue';
  if (status === 'waiting') return 'orange';
  if (status === 'cancelled') return 'transparent';
  if (status === 'interrupted') return 'orange';
  return 'transparent';
}

export function invocationStatusLabel(status: SubagentInvocationStatus): string {
  if (status === 'completed') return '已完成';
  if (status === 'failed') return '失败';
  if (status === 'running') return '运行中';
  if (status === 'queued') return '排队中';
  if (status === 'waiting') return '等待确认';
  if (status === 'cancelled') return '已取消';
  if (status === 'interrupted') return '后端重启中断';
  return status || 'unknown';
}

export function deliveryStatusLabel(status: SubagentDeliveryStatus): string {
  if (status === 'pending') return '等待主 Agent读取';
  if (status === 'delivered') return '已读取，等待消费';
  if (status === 'consumed') return '主 Agent已消费';
  return '尚无结果';
}

export function matchText(query: string, values: string[]): boolean {
  const normalized = query.trim().toLowerCase();
  if (!normalized) return true;
  return values.some((value) => value.toLowerCase().includes(normalized));
}
