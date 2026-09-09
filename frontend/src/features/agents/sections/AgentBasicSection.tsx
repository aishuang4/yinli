/**
 * AgentBasicSection — Agent 基础配置表单区块。
 *
 * 负责名称、描述和系统提示词字段，保持 AgentEditor 只做状态编排。
 */

import { FormGrid, SelectField, TextAreaField, TextField } from '../../../components/form';
import { applyJobRoleDefaults, type AgentCreatePayload, type AgentRole } from '../../../types/agent';

interface AgentBasicSectionProps {
  form: AgentCreatePayload;
  onChange: (form: AgentCreatePayload) => void;
  lockRole?: boolean;
}

export function AgentBasicSection({
  form,
  onChange,
  lockRole = false,
}: AgentBasicSectionProps) {
  return (
    <section className="rounded-xl bg-[var(--color-bg-primary)] p-4 shadow-sm">
      <div className="mb-4 flex items-center justify-between gap-3">
        <div>
          <h3 className="text-sm font-medium text-[var(--color-text-primary)]">
            基础配置
          </h3>
        </div>
      </div>
      <FormGrid>
        <TextField
          label="名称"
          required
          value={form.name}
          onChange={(event) => onChange({ ...form, name: event.target.value })}
          placeholder="如：CodeReviewer"
          maxLength={50}
        />
        <SelectField
          label="用途"
          value={form.role}
          disabled={lockRole}
          options={[
            { label: '对话助手', value: 'chat' },
            { label: '定时任务 Agent', value: 'job' },
          ]}
          onChange={(value) => onChange(applyJobRoleDefaults(form, String(value) as AgentRole))}
          help={lockRole
            ? `系统内置 Agent 的用途固定为${form.role === 'job' ? '定时任务 Agent' : '对话助手'}。`
            : 'Job Agent 不能在 Chat 里选用，专门给定时任务到点执行。'}
        />
        <TextField
          label="描述"
          value={form.description}
          onChange={(event) => onChange({ ...form, description: event.target.value })}
          placeholder="一句话描述这个 Agent 的用途"
          maxLength={200}
        />
        <TextAreaField
          className="col-span-1 md:col-span-2"
          label="系统提示词"
          value={form.systemPrompt}
          onChange={(event) => onChange({ ...form, systemPrompt: event.target.value })}
          placeholder="定义 Agent 的角色和行为..."
          rows={10}
          style={{ fontFamily: 'var(--font-mono)' }}
        />
      </FormGrid>
    </section>
  );
}
