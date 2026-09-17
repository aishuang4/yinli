/**
 * ScheduledJobHistory — firing 历史、产物 manifest、打开目录与取消在途执行。
 */

import { Ban, FolderOpen, List } from 'lucide-react';
import { Button, Tag } from '@/components/design';
import {
  WorkbenchEmptyPanel,
  WorkbenchList,
  WorkbenchListIcon,
  WorkbenchListRow,
  WorkbenchLoadingPanel,
} from '../../components/workbench';
import type { ScheduledJob, ScheduledJobRun } from '../../types/scheduledJob';
import {
  FIRING_STATUS_LABELS,
  firingStatusColor,
  formatDateTime,
  isInFlight,
  jobRunWorkspaceDir,
  manifestFilePath,
  manifestFiles,
} from './jobViewUtils';

interface ScheduledJobHistoryProps {
  job: ScheduledJob;
  runs: ScheduledJobRun[];
  loading: boolean;
  error: string | null;
  busyId: string | null;
  writesEnabled: boolean;
  canOpenDirectory: boolean;
  projectRoot: string;
  onCancel: (run: ScheduledJobRun) => void;
  onOpenDirectory: (targetPath: string) => void;
}

export function ScheduledJobHistory({
  job,
  runs,
  loading,
  error,
  busyId,
  writesEnabled,
  canOpenDirectory,
  projectRoot,
  onCancel,
  onOpenDirectory,
}: ScheduledJobHistoryProps) {
  if (error) {
    return (
      <WorkbenchEmptyPanel>
        <span className="text-[var(--color-error)]">{error}</span>
      </WorkbenchEmptyPanel>
    );
  }

  if (loading) {
    return <WorkbenchLoadingPanel tip="加载运行历史..." />;
  }

  if (runs.length === 0) {
    return <WorkbenchEmptyPanel>还没有运行记录</WorkbenchEmptyPanel>;
  }

  return (
    <WorkbenchList>
      {runs.map((run, index) => {
        const files = manifestFiles(run.outputManifest);
        const runDir = projectRoot ? jobRunWorkspaceDir(projectRoot, job.id, run.id) : '';
        const inflight = isInFlight(run);
        return (
          <div key={run.id} className={index === runs.length - 1 ? '' : 'border-b border-[var(--color-border)]'}>
            <WorkbenchListRow
              icon={(
                <WorkbenchListIcon>
                  <List size={18} strokeWidth={1.8} />
                </WorkbenchListIcon>
              )}
              title={FIRING_STATUS_LABELS[run.status] || run.status}
              subtitle={`${run.trigger} · ${formatDateTime(run.scheduledFor, job.timezone)}`}
              description={run.errorMessage || run.errorCode || run.chatRunId || '无附加信息'}
              last
              meta={(
                <Tag size="small" color={firingStatusColor(run.status)}>
                  {FIRING_STATUS_LABELS[run.status] || run.status}
                </Tag>
              )}
              trailing={(
                <>
                  {canOpenDirectory && runDir ? (
                    <Button
                      size="small"
                      icon={<FolderOpen size={13} />}
                      onClick={() => onOpenDirectory(runDir)}
                    >
                      打开目录
                    </Button>
                  ) : null}
                  {inflight ? (
                    <Button
                      size="small"
                      danger
                      icon={<Ban size={13} />}
                      disabled={!writesEnabled}
                      loading={busyId === run.id}
                      onClick={() => onCancel(run)}
                    >
                      取消
                    </Button>
                  ) : null}
                </>
              )}
            />
            {run.resultText ? (
              <div className="px-4 pb-4">
                <div className="mb-2 text-xs text-[var(--color-text-tertiary)]">运行结果</div>
                <pre className="max-h-[320px] overflow-auto whitespace-pre-wrap rounded-md bg-[var(--color-bg-primary)] px-3 py-2 text-xs leading-6 text-[var(--color-text-primary)]">
                  {run.resultText}
                </pre>
              </div>
            ) : null}
            {files.length > 0 ? (
              <div className="px-4 pb-4">
                <div className="mb-2 text-xs text-[var(--color-text-tertiary)]">产物 manifest</div>
                <div className="space-y-1 rounded-md bg-[var(--color-bg-primary)] px-3 py-2 text-xs text-[var(--color-text-primary)]">
                  {files.map((file) => {
                    const relative = manifestFilePath(file);
                    const bytes = file.bytes ?? file.size;
                    return (
                      <div key={relative || JSON.stringify(file)} className="flex items-center justify-between gap-3">
                        <code className="min-w-0 truncate">{relative || '未命名文件'}</code>
                        <span className="shrink-0 text-[var(--color-text-tertiary)]">
                          {typeof bytes === 'number' ? `${bytes} B` : ''}
                          {file.sha256 ? ` · ${file.sha256.slice(0, 10)}` : ''}
                        </span>
                      </div>
                    );
                  })}
                </div>
              </div>
            ) : Object.keys(run.outputManifest || {}).length > 0 ? (
              <pre className="mx-4 mb-4 overflow-auto rounded-md bg-[var(--color-bg-primary)] px-3 py-2 text-xs text-[var(--color-text-secondary)]">
                {JSON.stringify(run.outputManifest, null, 2)}
              </pre>
            ) : null}
          </div>
        );
      })}
    </WorkbenchList>
  );
}
