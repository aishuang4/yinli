/**
 * AgentModelAdvancedSection — Agent 执行上限区块。
 *
 * 负责 ReAct 循环上限。
 */

import { NumberField } from '../../../components/form';
import type { AgentCreatePayload } from '../../../types/agent';

interface AgentModelAdvancedSectionProps {
  form: AgentCreatePayload;
  onChange: (form: AgentCreatePayload) => void;
}

export function AgentModelAdvancedSection({
  form,
  onChange,
}: AgentModelAdvancedSectionProps) {
  return (
    <section className="rounded-xl bg-[var(--color-bg-primary)] p-4 shadow-sm">
      <div className="mb-4">
        <h3 className="text-sm font-medium text-[var(--color-text-primary)]">
          执行上限
        </h3>

      </div>
      <div className="space-y-3">
        <NumberField
          label="ReAct 最大循环次数"
          min={1}
          max={512}
          value={form.modelConfig.maxReactIters}
          onChange={(value) => onChange({
            ...form,
            modelConfig: { ...form.modelConfig, maxReactIters: Number(value ?? 40) },
          })}
          style={{ width: 160 }}
          help="复杂 Skills 或多工具任务建议 40-120；长任务可提高上限，但会增加耗时和费用。"
        />
      </div>
    </section>
  );
}
