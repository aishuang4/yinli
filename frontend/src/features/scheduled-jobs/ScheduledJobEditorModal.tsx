/**
 * 人工新建 / 修改定时任务。
 *
 * 新建写入待确认，确认启用后才会跑。修改时只提交有变化的字段：
 * 已启用或已暂停的任务若改了执行者、冻结提示词或 Skill，会回到待确认。
 */

import { useEffect, useState } from 'react';
import { Button, Modal } from '@/components/design';
import {
  FormError,
  FormGrid,
  FormSection,
  SelectField,
  TextAreaField,
  TextField,
} from '../../components/form';
import { useEnabledSkills } from '../../hooks/useSkills';
import type { Agent } from '../../types/agent';
import type { Skill } from '../../types/skill';
import type {
  ScheduleKind,
  ScheduledJob,
  ScheduledJobFieldPatch,
  ScheduledJobWritePayload,
} from '../../types/scheduledJob';
import { readClock, readWeekdays, WEEKDAY_OPTIONS } from './jobViewUtils';

interface ScheduledJobEditorModalProps {
  open: boolean;
  job: ScheduledJob | null;
  agents: Agent[];
  onCancel: () => void;
  onCreate: (payload: ScheduledJobWritePayload) => Promise<void>;
  onUpdate: (jobId: string, patch: ScheduledJobFieldPatch) => Promise<void>;
}

const KIND_OPTIONS = [
  { label: '每天', value: 'daily' },
  { label: '每周', value: 'weekly' },
  { label: '单次', value: 'once' },
];

const CLOCK_PATTERN = /^([01]\d|2[0-3]):[0-5]\d$/;
const ONCE_PATTERN = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?$/;

function skillOptions(skills: Skill[], selected: string[]) {
  const byId = new Map(skills.map((skill) => [skill.id, skill]));
  const ids = [...skills.map((skill) => skill.id)];
  for (const id of selected) {
    if (!byId.has(id)) ids.push(id);
  }
  return ids.map((id) => {
    const skill = byId.get(id);
    return { label: skill ? `${skill.name} (${id})` : id, value: id };
  });
}

function sameIdSet(left: string[], right: string[]): boolean {
  if (left.length !== right.length) return false;
  const seen = new Set(right);
  return left.every((id) => seen.has(id));
}

function preferredAgentId(agents: Agent[]): string {
  const jobs = agents.filter((agent) => agent.role === 'job');
  return jobs.find((agent) => agent.id === 'system-websearch-job')?.id
    || jobs[0]?.id
    || agents[0]?.id
    || '';
}

function agentChoices(agents: Agent[], currentId: string) {
  const jobs = agents.filter((agent) => agent.role === 'job');
  const pool = jobs.length > 0 ? jobs : agents;
  const options = pool.map((agent) => ({
    value: agent.id,
    label: agent.role === 'job' ? agent.name : `${agent.name}（对话）`,
  }));
  if (currentId && !options.some((item) => item.value === currentId)) {
    options.unshift({ value: currentId, label: currentId });
  }
  return options;
}

function suggestOnceAt(): string {
  const next = new Date(Date.now() + 60 * 60 * 1000);
  const pad = (value: number) => String(value).padStart(2, '0');
  return `${next.getFullYear()}-${pad(next.getMonth() + 1)}-${pad(next.getDate())}T${pad(next.getHours())}:${pad(next.getMinutes())}:00`;
}

export function ScheduledJobEditorModal({
  open,
  job,
  agents,
  onCancel,
  onCreate,
  onUpdate,
}: ScheduledJobEditorModalProps) {
  return (
    <Modal
      open={open}
      title={job ? '编辑定时任务' : '新建定时任务'}
      onCancel={onCancel}
      footer={null}
      width={720}
      destroyOnHidden
    >
      {open ? (
        <ScheduledJobEditorForm
          key={job?.id ?? 'create'}
          job={job}
          agents={agents}
          onCancel={onCancel}
          onCreate={onCreate}
          onUpdate={onUpdate}
        />
      ) : null}
    </Modal>
  );
}

function ScheduledJobEditorForm({
  job,
  agents,
  onCancel,
  onCreate,
  onUpdate,
}: Omit<ScheduledJobEditorModalProps, 'open'>) {
  const scriptJob = job?.mode === 'script';
  const { skills } = useEnabledSkills();
  const [name, setName] = useState(job?.name ?? '');
  const [summary, setSummary] = useState(job?.summary ?? '');
  const [agentId, setAgentId] = useState(job?.agentId || preferredAgentId(agents));
  const [scheduleKind, setScheduleKind] = useState<ScheduleKind>(job?.scheduleKind ?? 'daily');
  const [weekdays, setWeekdays] = useState<string[]>(() => {
    const current = job ? readWeekdays(job) : [];
    return current.length > 0 ? current : ['mon'];
  });
  const [at, setAt] = useState(() => {
    const current = job ? readClock(job) : '';
    return current || '09:00';
  });
  const [timezone, setTimezone] = useState(job?.timezone || 'Asia/Shanghai');
  const [frozenPrompt, setFrozenPrompt] = useState(job?.frozenPrompt ?? '');
  const [skillIds, setSkillIds] = useState<string[]>(job?.skillIds ?? []);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (job || agentId || agents.length === 0) return;
    setAgentId(preferredAgentId(agents));
  }, [agentId, agents, job]);

  const changeKind = (next: ScheduleKind) => {
    setScheduleKind(next);
    if (next === 'once' && !ONCE_PATTERN.test(at.trim())) {
      setAt(suggestOnceAt());
    }
    if (next !== 'once' && !CLOCK_PATTERN.test(at.trim())) {
      setAt('09:00');
    }
  };

  const handleSubmit = async () => {
    const trimmedName = name.trim();
    const trimmedZone = timezone.trim();
    const clock = at.trim();
    if (!trimmedName) {
      setError('请填写任务名称');
      return;
    }
    if (!trimmedZone) {
      setError('请填写时区');
      return;
    }
    if (scheduleKind === 'weekly' && weekdays.length === 0) {
      setError('请至少选择一个星期');
      return;
    }
    if (scheduleKind === 'once') {
      if (!ONCE_PATTERN.test(clock)) {
        setError('单次时间格式为 2026-10-21T10:36:00，不要带时区');
        return;
      }
    } else if (!CLOCK_PATTERN.test(clock)) {
      setError('时间格式必须为 HH:MM');
      return;
    }
    if (!scriptJob) {
      if (!agentId.trim()) {
        setError('请选择执行者');
        return;
      }
      if (!frozenPrompt.trim()) {
        setError('请填写冻结提示词');
        return;
      }
    }

    const scheduleExpr = scheduleKind === 'weekly'
      ? { weekdays, at: clock }
      : { at: clock };
    const scheduleChanged = !job
      || job.scheduleKind !== scheduleKind
      || job.timezone !== trimmedZone
      || JSON.stringify(job.scheduleKind === 'weekly' ? { weekdays: readWeekdays(job), at: readClock(job) } : { at: readClock(job) })
        !== JSON.stringify(scheduleExpr);

    setSubmitting(true);
    setError('');
    try {
      if (!job) {
        await onCreate({
          name: trimmedName,
          summary: summary.trim(),
          agentId,
          scheduleKind,
          scheduleExpr,
          timezone: trimmedZone,
          frozenPrompt: frozenPrompt.trim(),
          skillIds,
        });
      } else {
        const patch: ScheduledJobFieldPatch = {};
        if (trimmedName !== job.name) patch.name = trimmedName;
        if (summary.trim() !== job.summary) patch.summary = summary.trim();
        if (scheduleChanged) {
          patch.scheduleKind = scheduleKind;
          patch.scheduleExpr = scheduleExpr;
          patch.timezone = trimmedZone;
        }
        if (!scriptJob) {
          if (agentId !== job.agentId) patch.agentId = agentId;
          if (frozenPrompt.trim() !== job.frozenPrompt) patch.frozenPrompt = frozenPrompt.trim();
          if (!sameIdSet(skillIds, job.skillIds)) patch.skillIds = skillIds;
        }
        if (Object.keys(patch).length === 0) {
          setError('没有需要保存的改动');
          setSubmitting(false);
          return;
        }
        await onUpdate(job.id, patch);
      }
      onCancel();
    } catch (err) {
      setError(err instanceof Error ? err.message : '保存失败');
      setSubmitting(false);
    }
  };

  return (
    <div className="max-h-[70vh] space-y-3 overflow-y-auto pr-1">
      <FormError>{error}</FormError>
      <FormSection
        title="任务"
        description={job
          ? '已启用或已暂停时，改执行者、提示词或 Skill 会回到待确认。改名称、摘要或调度时间不会。'
          : '创建后是待确认。确认启用后才会按调度运行。'}
      >
        <FormGrid>
          <TextField
            label="名称"
            required
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
          <TextField
            label="摘要"
            value={summary}
            onChange={(event) => setSummary(event.target.value)}
          />
        </FormGrid>
      </FormSection>

      {scriptJob ? (
        <p className="text-xs text-[var(--color-text-tertiary)]">脚本任务只改名称、摘要和调度。</p>
      ) : (
        <FormSection
          title="执行者"
          description="到期使用这个 Agent 已经选好的必注入工具，不再另设一套工具范围。"
        >
          <SelectField
            label="Agent"
            required
            value={agentId || undefined}
            options={agentChoices(agents, agentId)}
            placeholder="选择执行者"
            onChange={(value) => setAgentId(String(value ?? ''))}
          />
          <div className="mt-3">
            <SelectField
              label="Skill"
              mode="multiple"
              allowClear
              value={skillIds}
              placeholder="选择要执行的 Skill，可多选"
              options={skillOptions(skills, skillIds)}
              onChange={(value) => setSkillIds((value as string[] | undefined) ?? [])}
              help="增加或删除都会改绑定。已启用或已暂停的任务保存后回到待确认，确认启用时重新冻结。"
            />
          </div>
        </FormSection>
      )}

      <FormSection title="调度" description="时间是所选时区下的钟面。单次时间不要带 +08:00 这类偏移。">
        <FormGrid>
          <SelectField
            label="类型"
            required
            value={scheduleKind}
            options={KIND_OPTIONS}
            onChange={(value) => changeKind(value as ScheduleKind)}
          />
          {scheduleKind === 'weekly' ? (
            <SelectField
              label="星期"
              required
              mode="multiple"
              value={weekdays}
              options={WEEKDAY_OPTIONS.map((item) => ({ label: item.label, value: item.value }))}
              onChange={(value) => setWeekdays((value as string[] | undefined) ?? [])}
            />
          ) : null}
          <TextField
            label={scheduleKind === 'once' ? '执行时间' : '时间'}
            required
            value={at}
            placeholder={scheduleKind === 'once' ? '2026-10-21T10:36:00' : '09:00'}
            onChange={(event) => setAt(event.target.value)}
          />
          <TextField
            label="时区"
            required
            value={timezone}
            placeholder="Asia/Shanghai"
            onChange={(event) => setTimezone(event.target.value)}
          />
        </FormGrid>
      </FormSection>

      {scriptJob ? null : (
        <>
          <FormSection title="冻结提示词" description="到期运行时使用这段内容，不会再跟 Chat 里的原话。">
            <TextAreaField
              value={frozenPrompt}
              rows={6}
              onChange={(event) => setFrozenPrompt(event.target.value)}
            />
          </FormSection>
        </>
      )}

      <div className="flex justify-end gap-2 pb-1">
        <Button disabled={submitting} onClick={onCancel}>取消</Button>
        <Button type="primary" loading={submitting} onClick={() => void handleSubmit()}>
          {job ? '保存' : '创建'}
        </Button>
      </div>
    </div>
  );
}
