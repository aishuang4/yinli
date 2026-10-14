/**
 * ScheduledJobScheduleForm — 编辑 weekly 调度（星期、钟面、时区）。
 */

import { useEffect, useState } from 'react';
import { Button } from '@/components/design';
import { FormSection, SelectField, TextField } from '../../components/form';
import type { ScheduledJob } from '../../types/scheduledJob';
import { readClock, readWeekdays, WEEKDAY_OPTIONS } from './jobViewUtils';

interface ScheduledJobScheduleFormProps {
  job: ScheduledJob;
  disabled: boolean;
  saving: boolean;
  onSave: (weekdays: string[], at: string, timezone: string) => Promise<void>;
}

export function ScheduledJobScheduleForm({
  job,
  disabled,
  saving,
  onSave,
}: ScheduledJobScheduleFormProps) {
  const [weekdays, setWeekdays] = useState<string[]>(readWeekdays(job));
  const [at, setAt] = useState(readClock(job));
  const [timezone, setTimezone] = useState(job.timezone);
  const [error, setError] = useState('');

  useEffect(() => {
    setWeekdays(readWeekdays(job));
    setAt(readClock(job));
    setTimezone(job.timezone);
    setError('');
  }, [job]);

  if (job.scheduleKind === 'daily') {
    return (
      <FormSection title="每天调度" description="保存会写回定义文件。时间是 timezone 下的 HH:MM。">
        <div className="grid gap-3 md:grid-cols-2">
          <TextField
            label="时间"
            required
            value={at}
            placeholder="09:00"
            disabled={disabled}
            onChange={(event) => setAt(event.target.value)}
          />
          <TextField
            label="时区"
            required
            value={timezone}
            placeholder="Asia/Shanghai"
            disabled={disabled}
            onChange={(event) => setTimezone(event.target.value)}
          />
        </div>
        {error ? <div className="text-sm text-[var(--color-error)]">{error}</div> : null}
        <div className="mt-3">
          <Button type="primary" size="small" disabled={disabled} loading={saving} onClick={() => void handleDailySave()}>
            保存调度
          </Button>
        </div>
      </FormSection>
    );
  }

  if (job.scheduleKind !== 'weekly') {
    return (
      <FormSection title="调度" description="Phase 1 仅支持在此修改 weekly 调度；单次任务保持只读。">
        <div className="text-sm text-[var(--color-text-secondary)]">
          单次执行时间：{readClock(job) || '未设置'}（{job.timezone}）
        </div>
      </FormSection>
    );
  }

  async function handleDailySave() {
    const clock = at.trim();
    if (!/^\d{2}:\d{2}$/.test(clock)) {
      setError('时间格式必须为 HH:MM');
      return;
    }
    if (!timezone.trim()) {
      setError('时区不能为空');
      return;
    }
    setError('');
    await onSave([], clock, timezone.trim());
  }

  async function handleSave() {
    const clock = at.trim();
    if (weekdays.length === 0) {
      setError('请至少选择一个星期');
      return;
    }
    if (!/^\d{2}:\d{2}$/.test(clock)) {
      setError('时间格式必须为 HH:MM');
      return;
    }
    if (!timezone.trim()) {
      setError('时区不能为空');
      return;
    }
    setError('');
    await onSave(weekdays, clock, timezone.trim());
  }

  return (
    <FormSection title="Weekly 调度" description="保存会写回定义文件；改调度可保持 active，不会回到待确认。">
      <div className="grid gap-3 md:grid-cols-2">
        <SelectField
          label="星期"
          required
          mode="multiple"
          value={weekdays}
          options={WEEKDAY_OPTIONS.map((item) => ({ label: item.label, value: item.value }))}
          placeholder="选择星期"
          disabled={disabled}
          onChange={(value) => setWeekdays((value as string[] | undefined) ?? [])}
        />
        <TextField
          label="时间"
          required
          value={at}
          placeholder="18:00"
          disabled={disabled}
          onChange={(event) => setAt(event.target.value)}
        />
        <TextField
          label="时区"
          required
          value={timezone}
          placeholder="Asia/Shanghai"
          help="IANA 时区，例如 Asia/Shanghai"
          disabled={disabled}
          className="md:col-span-2"
          onChange={(event) => setTimezone(event.target.value)}
        />
      </div>
      {error ? <div className="mt-2 text-xs text-[var(--color-error)]">{error}</div> : null}
      <div className="mt-3">
        <Button type="primary" size="small" disabled={disabled} loading={saving} onClick={() => void handleSave()}>
          保存调度
        </Button>
      </div>
    </FormSection>
  );
}
