/**
 * AgentJobToolkitSection — Job Agent 必注入 / 可选注入工具。
 */

import { Button, Tag } from '@/components/design';
import {
  JOB_BUILTIN_OPTIONS,
  type AgentCreatePayload,
  type JobToolkitItem,
  type JobToolkitKind,
} from '../../../types/agent';

interface AgentJobToolkitSectionProps {
  form: AgentCreatePayload;
  selectedMcp: Array<{ id: string; name: string }>;
  selectedHttp: Array<{ id: string; name: string }>;
  selectedSkills: Array<{ id: string; name: string }>;
  onChange: (form: AgentCreatePayload) => void;
  onOpenMcp: () => void;
  onOpenHttp: () => void;
  onOpenSkills: () => void;
}

function layerOf(form: AgentCreatePayload, kind: JobToolkitKind, id: string): 'required' | 'optional' | null {
  if (form.jobToolkit.required.some((item) => item.kind === kind && item.id === id)) return 'required';
  if (form.jobToolkit.optional.some((item) => item.kind === kind && item.id === id)) return 'optional';
  return null;
}

function upsertItem(
  form: AgentCreatePayload,
  item: JobToolkitItem,
  layer: 'required' | 'optional' | null,
): AgentCreatePayload {
  const without = {
    required: form.jobToolkit.required.filter((entry) => !(entry.kind === item.kind && entry.id === item.id)),
    optional: form.jobToolkit.optional.filter((entry) => !(entry.kind === item.kind && entry.id === item.id)),
  };
  if (layer === 'required') without.required = [...without.required, item];
  if (layer === 'optional') without.optional = [...without.optional, item];
  const mcpTools = [
    ...without.required.filter((entry) => entry.kind === 'mcp').map((entry) => entry.id),
    ...without.optional.filter((entry) => entry.kind === 'mcp').map((entry) => entry.id),
  ];
  const knowledgeTools = [
    ...without.required.filter((entry) => entry.kind === 'http' || entry.kind === 'knowledge').map((entry) => entry.id),
    ...without.optional.filter((entry) => entry.kind === 'http' || entry.kind === 'knowledge').map((entry) => entry.id),
  ];
  const skills = [
    ...without.required.filter((entry) => entry.kind === 'skill').map((entry) => entry.id),
    ...without.optional.filter((entry) => entry.kind === 'skill').map((entry) => entry.id),
  ];
  return {
    ...form,
    jobToolkit: without,
    mcpTools,
    knowledgeTools,
    skills,
  };
}

function LayerToggle({
  value,
  onChange,
}: {
  value: 'required' | 'optional' | null;
  onChange: (layer: 'required' | 'optional' | null) => void;
}) {
  return (
    <div className="flex shrink-0 gap-1">
      <button
        type="button"
        className={`rounded-md px-2 py-1 text-xs ${value === 'required' ? 'bg-[var(--color-accent-bg)] text-[var(--color-accent)]' : 'text-[var(--color-text-tertiary)]'}`}
        onClick={() => onChange(value === 'required' ? null : 'required')}
      >
        必注入
      </button>
      <button
        type="button"
        className={`rounded-md px-2 py-1 text-xs ${value === 'optional' ? 'bg-[var(--color-accent-bg)] text-[var(--color-accent)]' : 'text-[var(--color-text-tertiary)]'}`}
        onClick={() => onChange(value === 'optional' ? null : 'optional')}
      >
        可选
      </button>
    </div>
  );
}

export function AgentJobToolkitSection({
  form,
  selectedMcp,
  selectedHttp,
  selectedSkills,
  onChange,
  onOpenMcp,
  onOpenHttp,
  onOpenSkills,
}: AgentJobToolkitSectionProps) {
  return (
    <section className="rounded-xl bg-[var(--color-bg-primary)] p-4 shadow-sm">
      <div className="mb-3">
        <h3 className="text-sm font-medium text-[var(--color-text-primary)]">定时任务工具分层</h3>
        <p className="mt-1 text-xs leading-5 text-[var(--color-text-tertiary)]">
          必注入工具到期一定带上，门禁不能裁掉。可选工具才由 CheckJobFeasibility 按任务意图认领。
        </p>
      </div>
      <div className="space-y-3">
        {JOB_BUILTIN_OPTIONS.map((option) => (
          <div key={option.id} className="flex items-start justify-between gap-3 rounded-lg border border-[var(--color-border)] px-3 py-2">
            <div className="min-w-0">
              <div className="text-sm text-[var(--color-text-primary)]">{option.label}</div>
              <div className="mt-0.5 text-xs text-[var(--color-text-tertiary)]">{option.help}</div>
            </div>
            <LayerToggle
              value={layerOf(form, 'builtin', option.id)}
              onChange={(layer) => onChange(upsertItem(form, { kind: 'builtin', id: option.id }, layer))}
            />
          </div>
        ))}
      </div>
      <div className="mt-4 space-y-2">
        <div className="flex items-center justify-between">
          <span className="text-xs text-[var(--color-text-secondary)]">MCP / HTTP / Skill</span>
          <div className="flex gap-2">
            <Button size="small" onClick={onOpenMcp}>MCP</Button>
            <Button size="small" onClick={onOpenHttp}>HTTP</Button>
            <Button size="small" onClick={onOpenSkills}>Skills</Button>
          </div>
        </div>
        {[
          ...selectedMcp.map((item) => ({ ...item, kind: 'mcp' as const })),
          ...selectedHttp.map((item) => ({ ...item, kind: 'http' as const })),
          ...selectedSkills.map((item) => ({ ...item, kind: 'skill' as const })),
        ].map((item) => (
          <div key={`${item.kind}:${item.id}`} className="flex items-center justify-between gap-3 rounded-lg border border-[var(--color-border)] px-3 py-2">
            <div className="min-w-0">
              <div className="truncate text-sm text-[var(--color-text-primary)]">{item.name}</div>
              <Tag size="small" color="transparent">{item.kind}</Tag>
            </div>
            <LayerToggle
              value={layerOf(form, item.kind, item.id) || 'required'}
              onChange={(layer) => onChange(upsertItem(form, { kind: item.kind, id: item.id }, layer ?? 'required'))}
            />
          </div>
        ))}
        {selectedMcp.length + selectedHttp.length + selectedSkills.length === 0 ? (
          <div className="text-xs text-[var(--color-text-tertiary)]">还没有绑定 MCP / HTTP / Skill。内置工具可直接标为必注入。</div>
        ) : null}
      </div>
      {form.jobToolkit.required.length === 0 ? (
        <div className="mt-3 text-xs text-[var(--color-error)]">至少选择一项必注入工具，才能保存为 Job Agent。</div>
      ) : null}
    </section>
  );
}
