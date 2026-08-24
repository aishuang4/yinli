/**
 * ScheduledJobLandingPage — 定时任务第一层列表入口。
 */

import { CalendarClock, List, Pause, Play, RefreshCw, Trash2 } from 'lucide-react';
import { Button, Tag } from '@/components/design';
import { SelectField, TextField } from '../../components/form';
import {
  WorkbenchEmptyPanel,
  WorkbenchList,
  WorkbenchListIcon,
  WorkbenchListPage,
  WorkbenchListRow,
  WorkbenchLoadingPanel,
  WorkbenchSection,
} from '../../components/workbench';
import { SCHEDULER_DISABLED_DETAIL, type ScheduledJob } from '../../types/scheduledJob';
import {
  canWriteJob,
  formatDateTime,
  formatSchedule,
  JOB_STATUS_LABELS,
  jobStatusColor,
} from './jobViewUtils';

const STATUS_FILTER_OPTIONS = [
  { label: '全部状态', value: 'all' },
  { label: JOB_STATUS_LABELS.pending_confirmation, value: 'pending_confirmation' },
  { label: JOB_STATUS_LABELS.active, value: 'active' },
  { label: JOB_STATUS_LABELS.paused, value: 'paused' },
  { label: JOB_STATUS_LABELS.completed, value: 'completed' },
  { label: JOB_STATUS_LABELS.archived, value: 'archived' },
];

interface ScheduledJobLandingPageProps {
  jobs: ScheduledJob[];
  agentNames: Record<string, string>;
  loading: boolean;
  error: string | null;
  schedulerEnabled: boolean;
  busyId: string | null;
  query: string;
  statusFilter: string;
  onQueryChange: (value: string) => void;
  onStatusFilterChange: (value: string) => void;
  onRefresh: () => void;
  onSelect: (job: ScheduledJob) => void;
  onConfirm: (job: ScheduledJob) => void;
  onPause: (job: ScheduledJob) => void;
  onResume: (job: ScheduledJob) => void;
  onDelete: (job: ScheduledJob) => void;
}

export function ScheduledJobLandingPage({
  jobs,
  agentNames,
  loading,
  error,
  schedulerEnabled,
  busyId,
  query,
  statusFilter,
  onQueryChange,
  onStatusFilterChange,
  onRefresh,
  onSelect,
  onConfirm,
  onPause,
  onResume,
  onDelete,
}: ScheduledJobLandingPageProps) {
  const pendingCount = jobs.filter((job) => job.status === 'pending_confirmation').length;
  const activeCount = jobs.filter((job) => job.status === 'active').length;

  return (
    <WorkbenchListPage
      title="定时任务"
      icon={<CalendarClock size={18} />}
      actions={(
        <>
          <Tag size="small" color="blue">{jobs.length} 个任务</Tag>
          <Tag size="small" color="green">{activeCount} 个已启用</Tag>
          <Tag size="small" color="transparent">{pendingCount} 个待确认</Tag>
          <Button icon={<RefreshCw size={14} />} onClick={onRefresh} loading={loading}>
            刷新
          </Button>
        </>
      )}
    >
      {schedulerEnabled ? null : (
        <div className="mb-5">
          <WorkbenchEmptyPanel>
            {SCHEDULER_DISABLED_DETAIL}。当前只能查看任务，确认启用、暂停、删除和改调度不可用。
          </WorkbenchEmptyPanel>
        </div>
      )}

      <div className="mb-5 grid grid-cols-[minmax(240px,1fr)_180px] gap-3">
        <TextField
          value={query}
          placeholder="搜索任务名称或摘要"
          allowClear
          onChange={(event) => onQueryChange(event.target.value)}
        />
        <SelectField
          value={statusFilter}
          options={STATUS_FILTER_OPTIONS}
          onChange={(value) => onStatusFilterChange(String(value))}
        />
      </div>

      <WorkbenchSection title="Scheduled Jobs" compact>
        {error ? (
          <WorkbenchEmptyPanel>
            <span className="text-[var(--color-error)]">{error}</span>
          </WorkbenchEmptyPanel>
        ) : loading ? (
          <WorkbenchLoadingPanel tip="加载定时任务..." />
        ) : jobs.length === 0 ? (
          <WorkbenchEmptyPanel>
            {schedulerEnabled
              ? '暂无定时任务。在 Chat 中登记周期任务后，可在此确认启用。'
              : '调度未启用，暂无定时任务。'}
          </WorkbenchEmptyPanel>
        ) : (
          <WorkbenchList>
            {jobs.map((job, index) => (
              <ScheduledJobRow
                key={job.id}
                job={job}
                agentName={agentNames[job.agentId] || job.agentId}
                busy={busyId === job.id}
                last={index === jobs.length - 1}
                writesEnabled={schedulerEnabled && canWriteJob(job)}
                onSelect={() => onSelect(job)}
                onConfirm={() => onConfirm(job)}
                onPause={() => onPause(job)}
                onResume={() => onResume(job)}
                onDelete={() => onDelete(job)}
              />
            ))}
          </WorkbenchList>
        )}
      </WorkbenchSection>
    </WorkbenchListPage>
  );
}

function ScheduledJobRow({
  job,
  agentName,
  busy,
  last,
  writesEnabled,
  onSelect,
  onConfirm,
  onPause,
  onResume,
  onDelete,
}: {
  job: ScheduledJob;
  agentName: string;
  busy: boolean;
  last: boolean;
  writesEnabled: boolean;
  onSelect: () => void;
  onConfirm: () => void;
  onPause: () => void;
  onResume: () => void;
  onDelete: () => void;
}) {
  return (
    <WorkbenchListRow
      icon={(
        <WorkbenchListIcon>
          <CalendarClock size={20} strokeWidth={1.8} />
        </WorkbenchListIcon>
      )}
      title={job.name}
      subtitle={`${agentName} · ${formatSchedule(job)}`}
      description={job.summary || job.frozenPrompt || '暂无摘要'}
      last={last}
      layout="columns"
      onSelect={onSelect}
      meta={(
        <>
          <Tag size="small" color={jobStatusColor(job.status)}>
            {JOB_STATUS_LABELS[job.status] || job.status}
          </Tag>
          <Tag size="small" color="transparent">
            {job.nextRunAt ? `下次 ${formatDateTime(job.nextRunAt)}` : '无下次运行'}
          </Tag>
        </>
      )}
      trailing={(
        <>
          <button
            type="button"
            onClick={onSelect}
            className="icon-button h-8 w-8 rounded-md text-[var(--color-text-tertiary)] transition hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]"
            aria-label={`打开 ${job.name} 详情`}
          >
            <List size={17} />
          </button>
          {job.status === 'pending_confirmation' ? (
            <Button size="small" type="primary" loading={busy} disabled={!writesEnabled} onClick={onConfirm}>
              确认启用
            </Button>
          ) : null}
          {job.status === 'active' ? (
            <Button size="small" icon={<Pause size={13} />} loading={busy} disabled={!writesEnabled} onClick={onPause}>
              暂停
            </Button>
          ) : null}
          {job.status === 'paused' ? (
            <Button size="small" type="primary" icon={<Play size={13} />} loading={busy} disabled={!writesEnabled} onClick={onResume}>
              恢复
            </Button>
          ) : null}
          {job.status !== 'archived' ? (
            <button
              type="button"
              onClick={onDelete}
              disabled={!writesEnabled || busy}
              className="icon-button h-8 w-8 rounded-md text-[var(--color-text-tertiary)] transition hover:bg-[var(--color-error-bg)] hover:text-[var(--color-error)] disabled:opacity-40"
              aria-label={`删除 ${job.name}`}
            >
              <Trash2 size={15} />
            </button>
          ) : null}
        </>
      )}
    />
  );
}
