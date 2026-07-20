/**
 * AgentModelAdvancedSection — Agent 模型高级参数区块。
 *
 * 负责降级模型链、温度、Top P、输出长度和 ReAct 循环上限。
 */

import { Button } from '@agentscope-ai/design';
import { FormField, NumberField, SelectField, SliderField } from '../../../components/form';
import type { AgentCreatePayload, ModelOption } from '../../../types/agent';

interface AgentModelAdvancedSectionProps {
  form: AgentCreatePayload;
  fallbackModelOptions: ModelOption[];
  onChange: (form: AgentCreatePayload) => void;
}

export function AgentModelAdvancedSection({
  form,
  fallbackModelOptions,
  onChange,
}: AgentModelAdvancedSectionProps) {
  return (
    <section className="rounded-xl bg-[var(--color-bg-primary)] p-4 shadow-sm">
      <div className="mb-4">
        <h3 className="text-sm font-medium text-[var(--color-text-primary)]">
          模型高级参数
        </h3>
        <div className="mt-1 text-xs text-[var(--color-text-tertiary)]">
          配置降级模型链、生成随机性、输出长度和 ReAct 循环上限。
        </div>
      </div>
      <div className="space-y-3">
        <FormField label="降级模型链">
          <div className="space-y-2">
            {form.modelConfig.fallback.map((fallbackModel, index) => (
              <div key={index} className="flex gap-2">
                <SelectField
                  className="flex-1"
                  value={fallbackModel}
                  onChange={(value) => {
                    const nextFallback = [...form.modelConfig.fallback];
                    nextFallback[index] = String(value);
                    onChange({
                      ...form,
                      modelConfig: { ...form.modelConfig, fallback: nextFallback },
                    });
                  }}
                  options={fallbackModelOptions.map((model) => ({
                    label: model.name,
                    value: model.id,
                  }))}
                />
                {form.modelConfig.fallback.length > 1 && (
                  <Button
                    danger
                    type="text"
                    onClick={() => {
                      const nextFallback = form.modelConfig.fallback.filter((_, itemIndex) => itemIndex !== index);
                      onChange({
                        ...form,
                        modelConfig: { ...form.modelConfig, fallback: nextFallback },
                      });
                    }}
                  >
                    ×
                  </Button>
                )}
              </div>
            ))}
            <Button
              type="link"
              size="small"
              onClick={() => onChange({
                ...form,
                modelConfig: {
                  ...form.modelConfig,
                  fallback: [...form.modelConfig.fallback, 'deepseek-v4-pro'],
                },
              })}
            >
              + 添加降级模型
            </Button>
          </div>
        </FormField>

        <SliderField
          label={`温度: ${form.modelConfig.temperature.toFixed(1)}`}
          min={0}
          max={2}
          step={0.1}
          value={form.modelConfig.temperature}
          onChange={(value) => onChange({
            ...form,
            modelConfig: { ...form.modelConfig, temperature: value },
          })}
        />
        <div className="-mt-2 flex justify-between text-xs text-[var(--color-text-tertiary)]">
          <span>精确 (0)</span>
          <span>创意 (2)</span>
        </div>

        <SliderField
          label={`Top P: ${form.modelConfig.topP.toFixed(2)}`}
          min={0}
          max={1}
          step={0.05}
          value={form.modelConfig.topP}
          onChange={(value) => onChange({
            ...form,
            modelConfig: { ...form.modelConfig, topP: value },
          })}
        />
        <div className="-mt-2 flex justify-between text-xs text-[var(--color-text-tertiary)]">
          <span>保守 (0)</span>
          <span>多样 (1)</span>
        </div>

        <NumberField
          label="最大输出 Token"
          min={256}
          max={128000}
          value={form.modelConfig.maxTokens}
          onChange={(value) => onChange({
            ...form,
            modelConfig: { ...form.modelConfig, maxTokens: Number(value ?? 4096) },
          })}
          style={{ width: 160 }}
        />

        <NumberField
          label="ReAct 最大循环次数"
          min={1}
          max={120}
          value={form.modelConfig.maxReactIters}
          onChange={(value) => onChange({
            ...form,
            modelConfig: { ...form.modelConfig, maxReactIters: Number(value ?? 40) },
          })}
          style={{ width: 160 }}
          help="复杂 Skills 或多工具任务建议 40-80；过高会增加耗时和费用。"
        />
      </div>
    </section>
  );
}
