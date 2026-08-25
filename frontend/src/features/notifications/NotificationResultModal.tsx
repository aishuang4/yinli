/**
 * NotificationResultModal — 点击任务通知后展示本次运行结果。
 */

import { useEffect, useState } from 'react';
import { Button, Modal, Spinner, Tag } from '@/components/design';
import { MarkdownContent } from '../../components/MarkdownContent';
import { FIRING_STATUS_LABELS, firingStatusColor, formatDateTime } from '../scheduled-jobs/jobViewUtils';
import { NOTIFICATION_KIND_LABELS } from './notificationViewUtils';
import type { UserNotification } from '../../types/notification';
import type { ScheduledJobRun } from '../../types/scheduledJob';
import { getErrorMessage } from '../../utils/feedback';

interface NotificationResultModalProps {
  notification: UserNotification | null;
  onClose: () => void;
  onOpenJob?: (jobId: string) => void;
}

function readResultText(payload: Record<string, unknown>): string {
  const resultText = payload.resultText;
  if (typeof resultText === 'string' && resultText.trim()) return resultText;
  return '';
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
    resultText: String(raw.resultText ?? (manifest as { resultText?: unknown }).resultText ?? ''),
    deliveryStatus: String(raw.deliveryStatus ?? 'not_applicable'),
    startedAt: raw.startedAt ? String(raw.startedAt) : null,
    completedAt: raw.completedAt ? String(raw.completedAt) : null,
    createdAt: String(raw.createdAt ?? ''),
    updatedAt: String(raw.updatedAt ?? ''),
  };
}

export function NotificationResultModal({
  notification,
  onClose,
  onOpenJob,
}: NotificationResultModalProps) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [run, setRun] = useState<ScheduledJobRun | null>(null);

  useEffect(() => {
    if (!notification) {
      setRun(null);
      setError(null);
      setLoading(false);
      return;
    }
    if (!notification.jobId || !notification.firingId) {
      setRun(null);
      setError(null);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    setRun(null);
    void (async () => {
      try {
        const response = await fetch(
          `/api/scheduled-jobs/${encodeURIComponent(notification.jobId)}/runs/${encodeURIComponent(notification.firingId)}`,
        );
        if (!response.ok) {
          throw new Error(await response.text());
        }
        const payload = await response.json() as Record<string, unknown>;
        if (!cancelled) setRun(mapRun(payload));
      } catch (err) {
        if (!cancelled) setError(getErrorMessage(err, '加载运行结果失败'));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [notification]);

  const resultText = (
    run?.resultText
    || readResultText(notification?.payload || {})
    || notification?.body
    || ''
  ).trim();
  const status = run?.status;
  const failed = notification?.kind === 'job_failed' || status === 'failed';

  return (
    <Modal
      open={Boolean(notification)}
      title={notification?.title || '运行结果'}
      onCancel={onClose}
      footer={(
        <div className="flex justify-end gap-2">
          {notification?.jobId && onOpenJob ? (
            <Button onClick={() => onOpenJob(notification.jobId)}>查看任务详情</Button>
          ) : null}
          <Button type="primary" onClick={onClose}>关闭</Button>
        </div>
      )}
      width={720}
      destroyOnClose
    >
      {notification ? (
        <div className="notification-result">
          <div className="mb-3 flex flex-wrap items-center gap-2 text-xs text-[var(--color-text-tertiary)]">
            <Tag size="small" color={failed ? 'red' : status ? firingStatusColor(status) : 'blue'}>
              {status ? (FIRING_STATUS_LABELS[status] || status) : (NOTIFICATION_KIND_LABELS[notification.kind] || notification.kind)}
            </Tag>
            <span>{formatDateTime(run?.completedAt || run?.scheduledFor || notification.createdAt)}</span>
          </div>
          {loading ? (
            <div className="flex min-h-[160px] items-center justify-center gap-2 text-sm text-[var(--color-text-tertiary)]">
              <Spinner size="small" />
              正在加载运行结果…
            </div>
          ) : null}
          {!loading && error && !resultText ? (
            <div className="rounded-md bg-[var(--color-error-bg)] px-3 py-2 text-sm text-[var(--color-error)]">
              {error}
            </div>
          ) : null}
          {!loading && (run?.errorMessage || (failed && resultText)) ? (
            <div className="mb-3 rounded-md bg-[var(--color-error-bg)] px-3 py-2 text-sm leading-6 text-[var(--color-error)] whitespace-pre-wrap">
              {run?.errorMessage || resultText}
            </div>
          ) : null}
          {!loading && resultText && !failed ? (
            <div className="notification-result__body max-h-[60vh] overflow-auto rounded-lg bg-[var(--color-bg-secondary)] px-4 py-3">
              <MarkdownContent content={resultText} />
            </div>
          ) : null}
          {!loading && !resultText && !run?.errorMessage && !error ? (
            <div className="py-8 text-center text-sm text-[var(--color-text-tertiary)]">
              这次运行没有留下可展示的结果文本。
            </div>
          ) : null}
        </div>
      ) : null}
    </Modal>
  );
}
