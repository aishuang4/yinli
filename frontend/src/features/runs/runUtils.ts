/**
 * runUtils — Runs 页面展示辅助函数。
 */

import type { RunSummary } from './runTypes';

export const ACTIVE_RUN_STATUSES = new Set(['running', 'waiting', 'cancelling']);

export function statusText(status: string) {
  if (status === 'running') return '运行中';
  if (status === 'waiting') return '等待人工处理';
  if (status === 'cancelling') return '取消中';
  if (status === 'cancelled') return '已取消';
  if (status === 'completed') return '已完成';
  if (status === 'failed') return '失败';
  return status || '未知';
}

export function runDisplayName(run: RunSummary) {
  if (run.mode === 'team') {
    return run.teamName || run.agentName || '未命名 Team';
  }
  return run.agentName || '未命名 Agent';
}

export function formatDateTime(value: string) {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

export function formatDuration(start: string, end: string | null) {
  if (!start) return '';
  const startTime = new Date(start).getTime();
  const endTime = end ? new Date(end).getTime() : Date.now();
  if (Number.isNaN(startTime) || Number.isNaN(endTime) || endTime < startTime) return '';
  const seconds = Math.round((endTime - startTime) / 1000);
  if (seconds < 60) return `${seconds} 秒`;
  const minutes = Math.floor(seconds / 60);
  const restSeconds = seconds % 60;
  if (minutes < 60) return `${minutes} 分 ${restSeconds} 秒`;
  const hours = Math.floor(minutes / 60);
  return `${hours} 小时 ${minutes % 60} 分`;
}

export function clampProgress(value: number) {
  if (!Number.isFinite(value)) return 0;
  return Math.max(0, Math.min(100, Math.round(value)));
}
