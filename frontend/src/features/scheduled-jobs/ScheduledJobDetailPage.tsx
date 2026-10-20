/**
 * ScheduledJobDetailPage — 定时任务二级详情：规格、weekly 调度、历史与产物。
 */

import { CalendarClock, FolderOpen, Pause, Play, Trash2 } from 'lucide-react';
import { Button, Tag } from '@/components/design';
import { SelectField } from '../../components/form';
import { WorkbenchDetailShell, WorkbenchListIcon, WorkbenchSection } from '../../components/workbench';
import { useAuthStore } from '../auth/authStore';
import { modelAvailable, type ModelCatalog } from '../models/useModelCatalog';
import type { ScheduledJob, ScheduledJobRun } from '../../types/scheduledJob';
import { ScheduledJobContractPanel } from './ScheduledJobContractPanel';
import { ScheduledJobHistory } from './ScheduledJobHistory';
import { ScheduledJobScheduleForm } from './ScheduledJobScheduleForm';
import {
  canWriteJob,
  formatDateTime,
  formatSchedule,
  JOB_STATUS_LABELS,
  jobStatusColor,
  jobWorkspaceDir,
} from './jobViewUtils';

interface ScheduledJobDetailPageProps {
  job: ScheduledJob;
  agentName: string;
  runs: ScheduledJobRun[];
  runsLoading: boolean;
  runsError: string | null;
  busy: boolean;
  busyRunId: string | null;
  schedulerEnabled: boolean | null;
  canOpenDirectory: boolean;
  projectRoot: string;
  onBack: () => void;
  onConfirm: () => void;
  onPause: () => void;
  onResume: () => void;
  onDelete: () => void;
  catalog: ModelCatalog | null;
  onSaveSchedule: (weekdays: string[], at: string, timezone: string) => Promise<void>;
  onSaveModel: (modelConfigId: string) => Promise<void>;
  onCancelRun: (run: ScheduledJobRun) => void;
  onOpenDirectory: (targetPath: string) => void;
}

export function ScheduledJobDetailPage({
  job,
  agentName,
  runs,
  runsLoading,
  runsError,
  busy,
  busyRunId,
  schedulerEnabled,
  canOpenDirectory,
  projectRoot,
  onBack,
  onConfirm,
  onPause,
  onResume,
  onDelete,
  catalog,
  onSaveSchedule,
  onSaveModel,
  onCancelRun,
  onOpenDirectory,
}: ScheduledJobDetailPageProps) {
  const writable = schedulerEnabled === true && canWriteJob(job);
  const localExecution = useAuthStore((state) => state.capabilities.localExecution);
  const jobDir = projectRoot ? jobWorkspaceDir(projectRoot, job.id) : '';

  return (
    <WorkbenchDetailShell
      title={job.name}
      subtitle={job.summary || formatSchedule(job)}
      onBack={onBack}
      actions={(
        <>
          {job.status === 'pending_confirmation' ? (
            <Button size="small" type="primary" loading={busy} disabled={!writable} onClick={onConfirm}>
              确认启用
            </Button>
          ) : null}
          {job.status === 'active' ? (
            <Button size="small" icon={<Pause size={13} />} loading={busy} disabled={!writable} onClick={onPause}>
              暂停
            </Button>
          ) : null}
          {job.status === 'paused' ? (
            <Button size="small" type="primary" icon={<Play size={13} />} loading={busy} disabled={!writable} onClick={onResume}>
              恢复
            </Button>
          ) : null}
          {canOpenDirectory && jobDir ? (
            <Button size="small" icon={<FolderOpen size={13} />} onClick={() => onOpenDirectory(jobDir)}>
              打开目录
            </Button>
          ) : null}
          {job.status !== 'archived' ? (
            <Button size="small" danger icon={<Trash2 size={13} />} disabled={!writable} onClick={onDelete}>
              删除
            </Button>
          ) : null}
        </>
      )}
    >
      <div className="h-full min-h-0 overflow-y-auto p-5">
        <div className="mx-auto grid w-full max-w-5xl gap-4">
          <section className="rounded-xl bg-[var(--color-bg-primary)] p-4">
            <div className="mb-4 flex items-center gap-3">
              <WorkbenchListIcon>
                <CalendarClock size={20} strokeWidth={1.8} />
              </WorkbenchListIcon>
              <div className="min-w-0">
                <div className="truncate text-sm font-semibold text-[var(--color-text-primary)]">{job.name}</div>
                <div className="mt-1 flex flex-wrap gap-1">
                  <Tag size="small" color={jobStatusColor(job.status)}>
                    {JOB_STATUS_LABELS[job.status] || job.status}
                  </Tag>
                  <Tag size="small" color="transparent">{job.scheduleKind}</Tag>
                </div>
              </div>
            </div>
            <div className="grid gap-3 text-sm md:grid-cols-2">
              <DetailField label="Agent" value={agentName} />
              <DetailField label="调度" value={formatSchedule(job)} />
              <DetailField label="下次运行" value={formatDateTime(job.nextRunAt, job.timezone)} />
              <DetailField label="上次运行" value={formatDateTime(job.lastRunAt, job.timezone)} />
              <DetailField label="投递策略" value={job.deliveryPolicy} />
              <DetailField label="错过策略" value={job.missedPolicy} />
              <DetailField label="失败策略" value={job.failurePolicy} />
              <DetailField label="暂停原因" value={job.pauseReason || '无'} />
              <DetailField
                label="定义文件"
                value={job.sourcePath || '尚未写入 catalog'}
              />
            </div>
          </section>

          {job.status === 'pending_confirmation' ? (
            <section className="rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-primary)] p-4 text-sm text-[var(--color-text-secondary)]">
              任务字段已齐全。确认启用来自主路径；AskUser 收集字段不会自动把任务变成 active。
            </section>
          ) : null}

          <ScheduledJobContractPanel job={job} />

          {job.mode === 'agent' ? (
            <JobModelField
              job={job}
              catalog={catalog}
              disabled={!writable}
              saving={busy}
              onSave={onSaveModel}
            />
          ) : (!localExecution ? (
            <p className="text-sm text-[var(--color-text-secondary)]">在隔离沙箱中运行</p>
          ) : null)}

          <ScheduledJobScheduleForm
            job={job}
            disabled={!writable}
            saving={busy}
            onSave={onSaveSchedule}
          />

          <section className="rounded-xl bg-[var(--color-bg-primary)] p-4">
            <div className="mb-1 text-sm font-semibold text-[var(--color-text-primary)]">冻结提示词</div>
            <div className="mb-3 text-xs text-[var(--color-text-tertiary)]">
              定义权威是 Markdown 文件。改提示词、Agent 或策略请 Edit 定义文件，下一轮列表会读文件。
            </div>
            <pre className="max-h-[240px] overflow-auto whitespace-pre-wrap rounded-md bg-[var(--color-bg-secondary)] px-3 py-2 text-xs leading-6 text-[var(--color-text-primary)]">
              {job.frozenPrompt || '未配置'}
            </pre>
          </section>

          <section className="rounded-xl bg-[var(--color-bg-primary)] p-4">
            <div className="mb-3 text-sm font-semibold text-[var(--color-text-primary)]">工具范围</div>
            <div className="flex flex-wrap gap-2">
              {Object.entries(job.toolScope).map(([key, enabled]) => (
                <Tag key={key} size="small" color={enabled ? 'green' : 'transparent'}>
                  {key} {enabled ? '开' : '关'}
                </Tag>
              ))}
            </div>
            {job.skillIds.length > 0 ? (
              <div className="mt-3 text-xs text-[var(--color-text-tertiary)]">
                Skills：{job.skillIds.join('、')}
              </div>
            ) : (
              <div className="mt-3 text-xs text-[var(--color-text-tertiary)]">未冻结 Skill</div>
            )}
          </section>

          <section className="rounded-xl bg-[var(--color-bg-primary)] p-4">
            <div className="mb-1 text-sm font-semibold text-[var(--color-text-primary)]">Skill Context</div>
            <div className="mb-3 text-xs text-[var(--color-text-tertiary)]">
              每次 firing 使用独立 Context；正文在确认后按版本校验并进入会话 Context，不写入 system prompt。
            </div>
            <div className="mb-3 flex flex-wrap gap-2 text-xs">
              <Tag size="small" color="blue">策略：{job.contextPolicy}</Tag>
              <Tag size="small" color={job.requiredSkills.length > 0 ? 'green' : 'transparent'}>
                {job.requiredSkills.length > 0 ? `${job.requiredSkills.length} 个已冻结 Skill` : '未冻结 Skill'}
              </Tag>
            </div>
            {job.requiredSkills.length > 0 ? (
              <div className="space-y-2">
                {job.requiredSkills.map((skill) => (
                  <div key={skill.skillId} className="rounded-md bg-[var(--color-bg-secondary)] px-3 py-2 text-xs">
                    <div className="font-medium text-[var(--color-text-primary)]">{skill.skillId}</div>
                    <div className="mt-1 break-all text-[var(--color-text-tertiary)]">
                      SHA-256：{skill.contentSha256 || '待确认'}
                      {skill.contentChars != null ? ` · ${skill.contentChars} 字符` : ''}
                    </div>
                  </div>
                ))}
              </div>
            ) : null}
          </section>

          <WorkbenchSection title="运行历史" compact>
            <ScheduledJobHistory
              job={job}
              runs={runs}
              loading={runsLoading}
              error={runsError}
              busyId={busyRunId}
              writesEnabled={writable}
              canOpenDirectory={canOpenDirectory}
              projectRoot={projectRoot}
              onCancel={onCancelRun}
              onOpenDirectory={onOpenDirectory}
            />
          </WorkbenchSection>
        </div>
      </div>
    </WorkbenchDetailShell>
  );
}

function JobModelField({
  job,
  catalog,
  disabled,
  saving,
  onSave,
}: {
  job: ScheduledJob;
  catalog: ModelCatalog | null;
  disabled: boolean;
  saving: boolean;
  onSave: (modelConfigId: string) => Promise<void>;
}) {
  const options = (catalog?.models ?? [])
    .filter((item) => catalog && modelAvailable(catalog, item))
    .map((item) => ({ value: item.id, label: item.label || item.model }));
  return (
    <section className="rounded-xl bg-[var(--color-bg-primary)] p-4">
      <div className="mb-1 text-sm font-semibold text-[var(--color-text-primary)]">运行模型</div>
      <p className="mb-3 text-xs text-[var(--color-text-tertiary)]">
        到期执行用这里的模型。留空则用模型页的默认模型。没有自定义模型时，使用系统内置模型。
      </p>
      <div className="flex flex-wrap items-end gap-3">
        <div className="min-w-[240px] flex-1">
          <SelectField
            label="模型"
            aria-label="定时任务运行模型"
            value={job.modelConfigId || undefined}
            options={options}
            placeholder={options.length ? '使用默认模型' : '还没有可用模型'}
            disabled={disabled || options.length === 0}
            onChange={(value) => void onSave(String(value || ''))}
          />
        </div>
        {job.modelConfigId ? (
          <Button size="small" disabled={disabled || saving} onClick={() => void onSave('')}>
            改回默认
          </Button>
        ) : null}
      </div>
    </section>
  );
}

function DetailField({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md bg-[var(--color-bg-secondary)] p-3">
      <div className="text-xs text-[var(--color-text-tertiary)]">{label}</div>
      <div className="mt-1 break-all text-sm font-medium text-[var(--color-text-primary)]">{value}</div>
    </div>
  );
}
