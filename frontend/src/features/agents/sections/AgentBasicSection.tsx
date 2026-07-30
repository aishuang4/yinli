/**
 * AgentBasicSection — Agent 基础配置表单区块。
 *
 * 负责名称、描述、主模型和系统提示词字段，保持 AgentEditor 只做状态编排。
 */

import { Alert } from '@/components/design';
import { FormGrid, SelectField, TextAreaField, TextField } from '../../../components/form';
import type { AgentCreatePayload, ModelOption } from '../../../types/agent';

interface AgentBasicSectionProps {
  form: AgentCreatePayload;
  primaryModelOptions: ModelOption[];
  onChange: (form: AgentCreatePayload) => void;
}

export function AgentBasicSection({
  form,
  primaryModelOptions,
  onChange,
}: AgentBasicSectionProps) {
  return (
    <section className="rounded-xl bg-[var(--color-bg-primary)] p-4 shadow-sm">
      <div className="mb-4 flex items-center justify-between gap-3">
        <div>
          <h3 className="text-sm font-medium text-[var(--color-text-primary)]">
            基础配置
          </h3>
          <div className="mt-1 text-xs text-[var(--color-text-tertiary)]">
            只保留最常用的 Agent 信息；工具、权限、计划和模型参数放在高级设置中。
          </div>
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
        <TextField
          label="描述"
          value={form.description}
          onChange={(event) => onChange({ ...form, description: event.target.value })}
          placeholder="一句话描述这个 Agent 的用途"
          maxLength={200}
        />
        <div className="col-span-1 md:col-span-2">
          {primaryModelOptions.length === 0 && (
            <div className="mb-3">
              <Alert
                type="warning"
                message="当前系统设置中的供应商未配置可用模型。请先到系统设置为当前供应商配置 API Key 和模型。"
              />
            </div>
          )}
          <SelectField
            label="使用模型"
            value={form.modelConfig.primary}
            onChange={(value) => onChange({
              ...form,
              modelConfig: { ...form.modelConfig, primary: String(value) },
            })}
            options={primaryModelOptions.map((model) => ({
              label: `${model.name} - ${model.description}`,
              value: model.id,
            }))}
          />
        </div>
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
