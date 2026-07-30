/**
 * AgentBuilderWizard — 根据需求生成独立 Agent 草案的审核弹窗内容。
 *
 * 从 AgentsPage 抽离，统一 Builder 展示并减少页面文件体积。
 */

import { AlertCircle, CheckCircle2, Sparkles, WandSparkles } from 'lucide-react';
import { Button, Tag } from '@/components/design';
import { SelectField, TextAreaField } from '../../components/form';
import { DEFAULT_MODEL_CONFIG, type ModelsResponse } from '../../types/agent';
import type { BuilderPlan } from '../../types/agentBuilder';

interface AgentBuilderWizardProps {
  requirement: string;
  model: string;
  models: ModelsResponse | null;
  plan: BuilderPlan | null;
  planning: boolean;
  applying: boolean;
  error: string | null;
  onRequirementChange: (value: string) => void;
  onModelChange: (value: string) => void;
  onCreatePlan: () => Promise<void>;
  onApplyPlan: () => Promise<void>;
  onResetPlan: () => void;
}

export function AgentBuilderWizard({
  requirement,
  model,
  models,
  plan,
  planning,
  applying,
  error,
  onRequirementChange,
  onModelChange,
  onCreatePlan,
  onApplyPlan,
  onResetPlan,
}: AgentBuilderWizardProps) {
  const missingToolCount = plan?.agents.reduce((total, agent) => total + (agent.missingTools || []).length, 0) || 0;
  const missingSkillCount = plan?.agents.reduce((total, agent) => total + (agent.missingSkills || []).length, 0) || 0;

  return (
    <div
      className="grid min-h-0 grid-cols-[330px_1fr] gap-4 overflow-hidden"
      style={{ height: 'min(72vh, 720px)' }}
    >
      <div className="flex min-h-0 flex-col overflow-y-auto rounded-md bg-[var(--color-bg-secondary)] p-4 shadow-sm">
        <div className="mb-4 flex items-center gap-2 text-sm font-medium text-[var(--color-text-primary)]">
          <Sparkles size={15} color="var(--color-accent)" />
          <span>从需求生成可审核草案</span>
        </div>

        <div className="space-y-4">
          <SelectField
            label="规划模型"
            value={model}
            onChange={(value) => onModelChange(String(value))}
            options={[
              { label: '使用系统默认模型', value: '' },
              ...(models?.primary || []).map((item) => ({
                label: item.name,
                value: item.id,
              })),
            ]}
          />

          <TextAreaField
            label="用户需求"
            value={requirement}
            onChange={(event) => {
              onRequirementChange(event.target.value);
              if (plan) onResetPlan();
            }}
            placeholder="例如：创建一个能完成产品需求分析和技术方案评审的 Agent。"
            fieldClassName="h-52 resize-none"
          />

          {error && (
            <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-3 text-xs text-[var(--color-error)]">
              {error}
            </div>
          )}

          <Button
            type="primary"
            loading={planning}
            disabled={!requirement.trim() || applying}
            onClick={onCreatePlan}
            style={{ width: '100%' }}
          >
            <WandSparkles size={14} />
            生成草案
          </Button>
        </div>
      </div>

      <div className="h-full min-h-0 overflow-y-auto rounded-md bg-[var(--color-bg-secondary)] p-4 shadow-sm">
        {!plan ? (
          <div className="flex h-full items-center justify-center text-[var(--color-text-tertiary)]">
            <div className="text-center">
              <WandSparkles size={44} strokeWidth={1.2} className="mx-auto mb-3" />
              <div className="text-sm">输入需求后生成 Agent 草案</div>
              <div className="mt-1 text-xs">系统会匹配已启用的 MCP 工具和 Skills，并标出缺失能力</div>
            </div>
          </div>
        ) : (
          <div className="space-y-4 pb-2">
            <div className="flex items-start justify-between gap-4">
              <div>
                <div className="text-base font-medium text-[var(--color-text-primary)]">
                  Agent 创建草案
                </div>
                <div className="mt-1 text-sm text-[var(--color-text-secondary)]">
                  {plan.requirement}
                </div>
              </div>
              <div className="flex shrink-0 gap-2">
                <Tag size="small" color="transparent">Agents</Tag>
                <Tag size="small" color={missingToolCount + missingSkillCount > 0 ? 'orange' : 'green'}>
                  缺失 {missingToolCount + missingSkillCount}
                </Tag>
              </div>
            </div>

            {plan.warnings.length > 0 && (
              <div className="space-y-2 rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-3">
                {plan.warnings.map((warning) => (
                  <div key={warning} className="flex items-start gap-2 text-xs text-[var(--color-warning)]">
                    <AlertCircle size={13} className="mt-0.5 shrink-0" />
                    <span>{warning}</span>
                  </div>
                ))}
              </div>
            )}

            <div className="grid gap-3">
              {plan.agents.map((agent) => (
                <BuilderAgentDraftCard
                  key={agent.tempId}
                  agent={agent}
                />
              ))}
            </div>

            <div className="flex justify-end gap-2 rounded-md border border-[var(--color-border)] bg-[var(--color-bg-primary)] p-3 shadow-sm">
              <Button disabled={applying || planning} onClick={onResetPlan}>
                重新填写
              </Button>
              <Button type="primary" loading={applying} disabled={planning} onClick={onApplyPlan}>
                <CheckCircle2 size={14} />
                确认创建
              </Button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function BuilderAgentDraftCard({
  agent,
}: {
  agent: BuilderPlan['agents'][number];
}) {
  const matchedTools = (agent.recommendedTools || []).filter((item) => item.matched);
  const matchedSkills = (agent.recommendedSkills || []).filter((item) => item.matched);
  const modelConfig = agent.modelConfig || DEFAULT_MODEL_CONFIG;

  return (
    <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-4">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <Tag size="small" color="green">
              Agent
            </Tag>
            <div className="truncate text-sm font-medium text-[var(--color-text-primary)]">
              {agent.name}
            </div>
          </div>
          <div className="mt-2 text-xs text-[var(--color-text-secondary)]">
            {agent.description || '暂无描述'}
          </div>
        </div>
        <Tag size="small" color="transparent">
          {modelConfig.primary}
        </Tag>
      </div>

      <div className="mb-3 line-clamp-3 whitespace-pre-wrap rounded border border-[var(--color-border)] bg-[var(--color-bg-primary)] p-3 text-xs text-[var(--color-text-tertiary)]">
        {agent.systemPrompt}
      </div>

      <div className="grid gap-3 md:grid-cols-2">
        <CapabilityBlock
          title="MCP 工具"
          matched={matchedTools.map((item) => item.name)}
          missing={agent.missingTools || []}
        />
        <CapabilityBlock
          title="Skills"
          matched={matchedSkills.map((item) => item.name)}
          missing={agent.missingSkills || []}
        />
      </div>
    </div>
  );
}

function CapabilityBlock({
  title,
  matched,
  missing,
}: {
  title: string;
  matched: string[];
  missing: string[];
}) {
  return (
    <div>
      <div className="mb-2 text-xs text-[var(--color-text-tertiary)]">{title}</div>
      <div className="flex flex-wrap gap-1">
        {matched.length === 0 && missing.length === 0 && (
          <Tag size="small" color="transparent">未绑定</Tag>
        )}
        {matched.map((name) => (
          <Tag key={`matched-${name}`} size="small" color="green">
            {name}
          </Tag>
        ))}
        {missing.map((name) => (
          <Tag key={`missing-${name}`} size="small" color="orange">
            缺失 {name}
          </Tag>
        ))}
      </div>
    </div>
  );
}
