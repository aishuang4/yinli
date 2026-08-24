/**
 * useScheduledJobs — 定时任务 REST Hook。
 *
 * 按当前 Project 过滤；flag 关闭时写接口 403，页面展示只读/空状态，不崩溃。
 */

import { useCallback, useEffect, useState } from 'react';
import { useAppStore } from '../stores/appStore';
import {
  SCHEDULER_DISABLED_DETAIL,
  type JobToolScope,
  type ScheduledJob,
  type ScheduledJobRun,
  type ScheduledJobSchedulePatch,
} from '../types/scheduledJob';
import { getErrorMessage, notifyError, notifySuccess } from '../utils/feedback';

const FLAG_PROBE_JOB_ID = '__scheduler_flag__';

interface UseScheduledJobsReturn {
  jobs: ScheduledJob[];
  loading: boolean;
  error: string | null;
  schedulerEnabled: boolean;
  refresh: () => Promise<void>;
  confirmJob: (jobId: string) => Promise<ScheduledJob>;
  pauseJob: (jobId: string) => Promise<ScheduledJob>;
  resumeJob: (jobId: string) => Promise<ScheduledJob>;
  deleteJob: (jobId: string) => Promise<ScheduledJob>;
  updateSchedule: (jobId: string, patch: ScheduledJobSchedulePatch) => Promise<ScheduledJob>;
  listRuns: (jobId: string) => Promise<ScheduledJobRun[]>;
  cancelRun: (jobId: string, firingId: string) => Promise<ScheduledJobRun>;
}

class ScheduledJobsApiError extends Error {
  status: number;
  disabled: boolean;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
    this.disabled = status === 403 && message.includes(SCHEDULER_DISABLED_DETAIL);
  }
}

function readApiDetail(payload: unknown, status: number): string {
  if (!payload || typeof payload !== 'object') return `HTTP ${status}`;
  const detail = (payload as { detail?: unknown }).detail;
  if (typeof detail === 'string' && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    const messages = detail.map((item) => {
      if (typeof item === 'string') return item;
      if (item && typeof item === 'object' && 'msg' in item && typeof (item as { msg?: unknown }).msg === 'string') {
        return (item as { msg: string }).msg;
      }
      return '';
    }).filter(Boolean);
    if (messages.length > 0) return messages.join('; ');
  }
  return `HTTP ${status}`;
}

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) {
    const payload = await res.json().catch(() => ({}));
    throw new ScheduledJobsApiError(readApiDetail(payload, res.status), res.status);
  }
  return res.json() as Promise<T>;
}

function readBool(value: unknown): boolean {
  return value === true;
}

function mapToolScope(raw: unknown): JobToolScope {
  const source = raw && typeof raw === 'object' ? raw as Record<string, unknown> : {};
  return {
    allowBash: readBool(source.allowBash ?? source.allow_bash),
    allowHttp: readBool(source.allowHttp ?? source.allow_http),
    allowWeb: readBool(source.allowWeb ?? source.allow_web),
    allowMcp: readBool(source.allowMcp ?? source.allow_mcp),
    allowKnowledge: readBool(source.allowKnowledge ?? source.allow_knowledge),
  };
}

function mapJob(raw: Record<string, unknown>): ScheduledJob {
  return {
    id: String(raw.id ?? ''),
    workspaceId: String(raw.workspaceId ?? ''),
    projectId: String(raw.projectId ?? ''),
    name: String(raw.name ?? ''),
    summary: String(raw.summary ?? ''),
    status: raw.status as ScheduledJob['status'],
    pauseReason: String(raw.pauseReason ?? ''),
    agentId: String(raw.agentId ?? ''),
    mode: 'agent',
    scheduleKind: raw.scheduleKind === 'once' ? 'once' : 'weekly',
    scheduleExpr: (raw.scheduleExpr && typeof raw.scheduleExpr === 'object'
      ? raw.scheduleExpr
      : {}) as ScheduledJob['scheduleExpr'],
    timezone: String(raw.timezone ?? ''),
    nextRunAt: raw.nextRunAt ? String(raw.nextRunAt) : null,
    lastRunAt: raw.lastRunAt ? String(raw.lastRunAt) : null,
    frozenPrompt: String(raw.frozenPrompt ?? ''),
    skillIds: Array.isArray(raw.skillIds) ? raw.skillIds.map((item) => String(item)) : [],
    toolScope: mapToolScope(raw.toolScope),
    inputSpec: raw.inputSpec && typeof raw.inputSpec === 'object' ? raw.inputSpec as Record<string, unknown> : {},
    outputSpec: raw.outputSpec && typeof raw.outputSpec === 'object' ? raw.outputSpec as Record<string, unknown> : {},
    missedPolicy: (raw.missedPolicy as ScheduledJob['missedPolicy']) || 'catch_up_once',
    failurePolicy: (raw.failurePolicy as ScheduledJob['failurePolicy']) || 'notify_keep_schedule',
    deliveryPolicy: (raw.deliveryPolicy as ScheduledJob['deliveryPolicy']) || 'notify',
    deliveryChannelRef: String(raw.deliveryChannelRef ?? ''),
    recipientIds: Array.isArray(raw.recipientIds) ? raw.recipientIds.map((item) => String(item)) : [],
    unattendedPermission: String(raw.unattendedPermission ?? 'overlay_workspace_write'),
    autoSendEnabledAt: raw.autoSendEnabledAt ? String(raw.autoSendEnabledAt) : null,
    requiredSuccesses: Number(raw.requiredSuccesses ?? 3),
    consecutiveSuccesses: Number(raw.consecutiveSuccesses ?? 0),
    consecutiveFailures: Number(raw.consecutiveFailures ?? 0),
    maxConsecutiveFailures: Number(raw.maxConsecutiveFailures ?? 3),
    maxFiringsPerDay: Number(raw.maxFiringsPerDay ?? 8),
    createdFromSessionId: String(raw.createdFromSessionId ?? ''),
    createdAt: String(raw.createdAt ?? ''),
    updatedAt: String(raw.updatedAt ?? ''),
    archivedAt: raw.archivedAt ? String(raw.archivedAt) : null,
  };
}

function mapRun(raw: Record<string, unknown>): ScheduledJobRun {
  const manifest = raw.outputManifest && typeof raw.outputManifest === 'object'
    ? raw.outputManifest as ScheduledJobRun['outputManifest']
    : {};
  return {
    id: String(raw.id ?? ''),
    jobId: String(raw.jobId ?? ''),
    workspaceId: String(raw.workspaceId ?? ''),
    projectId: String(raw.projectId ?? ''),
    trigger: (raw.trigger as ScheduledJobRun['trigger']) || 'schedule',
    scheduledFor: String(raw.scheduledFor ?? ''),
    status: raw.status as ScheduledJobRun['status'],
    chatSessionId: String(raw.chatSessionId ?? ''),
    chatRunId: String(raw.chatRunId ?? ''),
    errorCode: String(raw.errorCode ?? ''),
    errorMessage: String(raw.errorMessage ?? ''),
    outputManifest: manifest,
    deliveryStatus: String(raw.deliveryStatus ?? 'not_applicable'),
    startedAt: raw.startedAt ? String(raw.startedAt) : null,
    completedAt: raw.completedAt ? String(raw.completedAt) : null,
    createdAt: String(raw.createdAt ?? ''),
    updatedAt: String(raw.updatedAt ?? ''),
  };
}

async function probeSchedulerEnabled(): Promise<boolean> {
  try {
    await requestJson(`/api/scheduled-jobs/${encodeURIComponent(FLAG_PROBE_JOB_ID)}/confirm`, {
      method: 'POST',
    });
    return true;
  } catch (err) {
    if (err instanceof ScheduledJobsApiError && err.disabled) return false;
    return true;
  }
}

export function useScheduledJobs(): UseScheduledJobsReturn {
  const activeProjectId = useAppStore((state) => state.activeProjectId);
  const [jobs, setJobs] = useState<ScheduledJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [schedulerEnabled, setSchedulerEnabled] = useState(false);

  const updateJob = useCallback((job: ScheduledJob) => {
    setJobs((prev) => {
      if (prev.some((item) => item.id === job.id)) {
        return prev.map((item) => (item.id === job.id ? job : item));
      }
      return [job, ...prev];
    });
  }, []);

  const refresh = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const params = new URLSearchParams();
      if (activeProjectId) params.set('projectId', activeProjectId);
      const query = params.toString();
      const [payload, enabled] = await Promise.all([
        requestJson<{ jobs: Record<string, unknown>[] }>(
          `/api/scheduled-jobs${query ? `?${query}` : ''}`,
        ),
        probeSchedulerEnabled(),
      ]);
      setJobs((payload.jobs || []).map(mapJob));
      setSchedulerEnabled(enabled);
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载定时任务失败');
      setJobs([]);
    } finally {
      setLoading(false);
    }
  }, [activeProjectId]);

  const runWrite = useCallback(async (
    action: () => Promise<ScheduledJob>,
    successMessage: string,
    failureFallback: string,
  ): Promise<ScheduledJob> => {
    try {
      const job = await action();
      updateJob(job);
      notifySuccess(successMessage);
      return job;
    } catch (err) {
      if (err instanceof ScheduledJobsApiError && err.disabled) {
        setSchedulerEnabled(false);
      }
      notifyError(`${failureFallback}：${getErrorMessage(err, '未知错误')}`);
      throw err;
    }
  }, [updateJob]);

  const confirmJob = useCallback((jobId: string) => (
    runWrite(
      () => requestJson<Record<string, unknown>>(`/api/scheduled-jobs/${encodeURIComponent(jobId)}/confirm`, {
        method: 'POST',
      }).then(mapJob),
      '已确认启用',
      '确认启用失败',
    )
  ), [runWrite]);

  const pauseJob = useCallback((jobId: string) => (
    runWrite(
      () => requestJson<Record<string, unknown>>(`/api/scheduled-jobs/${encodeURIComponent(jobId)}/pause`, {
        method: 'POST',
      }).then(mapJob),
      '已暂停任务',
      '暂停失败',
    )
  ), [runWrite]);

  const resumeJob = useCallback((jobId: string) => (
    runWrite(
      () => requestJson<Record<string, unknown>>(`/api/scheduled-jobs/${encodeURIComponent(jobId)}/resume`, {
        method: 'POST',
      }).then(mapJob),
      '已恢复任务',
      '恢复失败',
    )
  ), [runWrite]);

  const deleteJob = useCallback((jobId: string) => (
    runWrite(
      () => requestJson<Record<string, unknown>>(`/api/scheduled-jobs/${encodeURIComponent(jobId)}`, {
        method: 'DELETE',
      }).then(mapJob),
      '已归档任务',
      '删除失败',
    )
  ), [runWrite]);

  const updateSchedule = useCallback((jobId: string, patch: ScheduledJobSchedulePatch) => (
    runWrite(
      () => requestJson<Record<string, unknown>>(`/api/scheduled-jobs/${encodeURIComponent(jobId)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
      }).then(mapJob),
      '已更新调度',
      '更新调度失败',
    )
  ), [runWrite]);

  const listRuns = useCallback(async (jobId: string): Promise<ScheduledJobRun[]> => {
    const payload = await requestJson<{ runs: Record<string, unknown>[] }>(
      `/api/scheduled-jobs/${encodeURIComponent(jobId)}/runs`,
    );
    return (payload.runs || []).map(mapRun);
  }, []);

  const cancelRun = useCallback(async (jobId: string, firingId: string): Promise<ScheduledJobRun> => {
    try {
      const run = mapRun(await requestJson<Record<string, unknown>>(
        `/api/scheduled-jobs/${encodeURIComponent(jobId)}/runs/${encodeURIComponent(firingId)}/cancel`,
        { method: 'POST' },
      ));
      notifySuccess('已取消本次执行');
      return run;
    } catch (err) {
      if (err instanceof ScheduledJobsApiError && err.disabled) {
        setSchedulerEnabled(false);
      }
      notifyError(`取消执行失败：${getErrorMessage(err, '未知错误')}`);
      throw err;
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return {
    jobs,
    loading,
    error,
    schedulerEnabled,
    refresh,
    confirmJob,
    pauseJob,
    resumeJob,
    deleteJob,
    updateSchedule,
    listRuns,
    cancelRun,
  };
}

export type { UseScheduledJobsReturn };
