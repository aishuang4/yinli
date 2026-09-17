/**
 * 定时任务展示 helper：状态文案、调度摘要、产物目录路径。
 */

import type {
  FiringStatus,
  JobOutputFile,
  JobOutputManifest,
  JobStatus,
  ScheduledJob,
  ScheduledJobRun,
} from '../../types/scheduledJob';

export const JOB_STATUS_LABELS: Record<JobStatus, string> = {
  pending_confirmation: '待确认启用',
  active: '已启用',
  paused: '已暂停',
  archived: '已归档',
  completed: '已完成',
};

export const FIRING_STATUS_LABELS: Record<FiringStatus, string> = {
  queued: '排队中',
  starting: '启动中',
  running: '执行中',
  succeeded: '成功',
  failed: '失败',
  cancelled: '已取消',
  missed: '已错过',
  skipped: '已跳过',
};

export const WEEKDAY_OPTIONS = [
  { label: '周一', value: 'mon' },
  { label: '周二', value: 'tue' },
  { label: '周三', value: 'wed' },
  { label: '周四', value: 'thu' },
  { label: '周五', value: 'fri' },
  { label: '周六', value: 'sat' },
  { label: '周日', value: 'sun' },
] as const;

export const WEEKDAY_LABELS: Record<string, string> = Object.fromEntries(
  WEEKDAY_OPTIONS.map((item) => [item.value, item.label]),
);

export const NON_TERMINAL_FIRING_STATUSES = new Set<FiringStatus>(['queued', 'starting', 'running']);

export function jobStatusColor(status: JobStatus): string {
  if (status === 'active') return 'green';
  if (status === 'pending_confirmation') return 'blue';
  if (status === 'paused') return 'orange';
  if (status === 'completed') return 'purple';
  return 'transparent';
}

export function firingStatusColor(status: FiringStatus): string {
  if (status === 'succeeded') return 'green';
  if (status === 'running' || status === 'starting' || status === 'queued') return 'blue';
  if (status === 'failed' || status === 'missed') return 'red';
  if (status === 'cancelled' || status === 'skipped') return 'orange';
  return 'transparent';
}

export function formatDateTime(
  value: string | null | undefined,
  timeZone?: string | null,
): string {
  if (!value) return '未设置';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString('zh-CN', {
    hour12: false,
    ...(timeZone ? { timeZone } : {}),
  });
}

export function readWeekdays(job: ScheduledJob): string[] {
  const expr = job.scheduleExpr;
  const weekdays = 'weekdays' in expr ? expr.weekdays : undefined;
  if (!Array.isArray(weekdays)) return [];
  return weekdays.map((item) => String(item).toLowerCase()).filter(Boolean);
}

export function readClock(job: ScheduledJob): string {
  const expr = job.scheduleExpr;
  const at = 'at' in expr ? expr.at : undefined;
  return typeof at === 'string' ? at : '';
}

export function formatSchedule(job: ScheduledJob): string {
  if (job.scheduleKind === 'weekly') {
    const days = readWeekdays(job).map((item) => WEEKDAY_LABELS[item] || item).join('、') || '未选星期';
    return `每周 ${days} ${readClock(job) || '--:--'}（${job.timezone}）`;
  }
  return `单次 ${readClock(job) || '未设置'}（${job.timezone}）`;
}

export function joinProjectPath(root: string, ...parts: string[]): string {
  const usesBackslash = root.includes('\\') && !root.includes('/');
  const sep = usesBackslash ? '\\' : '/';
  const trimmed = root.replace(/[\\/]+$/, '');
  const rest = parts.map((part) => part.replace(/^[\\/]+|[\\/]+$/g, '')).filter(Boolean);
  return [trimmed, ...rest].join(sep);
}

export function jobWorkspaceDir(projectRoot: string, jobId: string): string {
  return joinProjectPath(projectRoot, '.agent-workstation', 'jobs', jobId);
}

export function jobRunWorkspaceDir(projectRoot: string, jobId: string, firingId: string): string {
  return joinProjectPath(projectRoot, '.agent-workstation', 'jobs', jobId, 'runs', firingId);
}

export function manifestFiles(manifest: JobOutputManifest | undefined): JobOutputFile[] {
  if (!manifest || !Array.isArray(manifest.files)) return [];
  return manifest.files.filter((item) => item && typeof item === 'object');
}

export function manifestFilePath(file: JobOutputFile): string {
  return file.relativePath || file.path || '';
}

export function canWriteJob(job: ScheduledJob): boolean {
  return job.status !== 'archived' && job.status !== 'completed';
}

export function isInFlight(run: ScheduledJobRun): boolean {
  return NON_TERMINAL_FIRING_STATUSES.has(run.status);
}
