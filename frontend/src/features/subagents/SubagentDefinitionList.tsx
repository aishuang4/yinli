/**
 * SubagentDefinitionList — 展示文件定义加载结果。
 */

import { AlertTriangle, Bot, ChevronRight, ShieldCheck } from 'lucide-react';
import { Empty, Tag } from '@/components/design';
import type { SubagentDefinition, SubagentLoadError } from '../../types/subagent';

interface SubagentDefinitionListProps {
  definitions: SubagentDefinition[];
  errors: SubagentLoadError[];
  warnings: string[];
  selectedName: string;
  invocationCounts: Record<string, number>;
  onSelect: (name: string) => void;
}

export function SubagentDefinitionList({
  definitions,
  errors,
  warnings,
  selectedName,
  invocationCounts,
  onSelect,
}: SubagentDefinitionListProps) {
  return (
    <section className="grid min-h-0 grid-rows-[auto_1fr] overflow-hidden border-r border-[var(--color-border)] bg-[var(--color-bg-secondary)]">
      <div className="flex items-center justify-between gap-3 border-b border-[var(--color-border)] px-4 py-3">
        <div className="flex items-center gap-2">
          <Bot size={16} color="var(--color-accent)" />
          <h2 className="text-sm font-semibold text-[var(--color-text-primary)]">定义目录</h2>
        </div>
        <div className="flex items-center gap-2">
          <Tag size="small" color="blue">{definitions.length}</Tag>
          {errors.length > 0 && <Tag size="small" color="red">{errors.length} 个错误</Tag>}
          {warnings.length > 0 && <Tag size="small" color="orange">{warnings.length} 个警告</Tag>}
        </div>
      </div>

      <div className="min-h-0 overflow-y-auto p-4">
        {(errors.length > 0 || warnings.length > 0) && (
          <div className="mb-4 grid gap-2">
            {errors.map((error) => (
              <div key={`${error.scope}:${error.path}`} className="rounded-md border border-[var(--color-error)] bg-[var(--color-bg-primary)] p-3 text-xs">
                <div className="flex items-center gap-2 font-medium text-[var(--color-error)]">
                  <AlertTriangle size={14} />
                  {error.scope} · {error.message}
                </div>
                <div className="mt-1 break-all text-[var(--color-text-tertiary)]">{error.path}</div>
              </div>
            ))}
            {warnings.map((warning) => (
              <div key={warning} className="rounded-md border border-[var(--color-warning)] bg-[var(--color-bg-primary)] p-3 text-xs text-[var(--color-text-secondary)]">
                <div className="flex items-center gap-2">
                  <AlertTriangle size={14} color="var(--color-warning)" />
                  {warning}
                </div>
              </div>
            ))}
          </div>
        )}

        {definitions.length === 0 ? (
          <div className="flex min-h-[320px] items-center justify-center">
            <Empty type="noData" description="暂无 Subagent 定义" />
          </div>
        ) : (
          <div className="grid gap-2">
            {definitions.map((definition) => (
              <SubagentDefinitionListItem
                key={`${definition.scope}:${definition.name}`}
                definition={definition}
                selected={definition.name === selectedName}
                invocationCount={invocationCounts[definition.name] || 0}
                onSelect={onSelect}
              />
            ))}
          </div>
        )}
      </div>
    </section>
  );
}

function SubagentDefinitionListItem({
  definition,
  selected,
  invocationCount,
  onSelect,
}: {
  definition: SubagentDefinition;
  selected: boolean;
  invocationCount: number;
  onSelect: (name: string) => void;
}) {
  const allowedTools = definition.tools?.length ? definition.tools : ['继承主 Agent 可用工具'];
  const hasMetadata = Object.keys(definition.metadata || {}).length > 0;

  return (
    <button
      type="button"
      onClick={() => onSelect(definition.name)}
      className={`w-full rounded-md border p-3 text-left transition-colors duration-200 ${
        selected
          ? 'border-[var(--color-accent)] bg-[var(--color-bg-primary)] shadow-sm'
          : 'border-[var(--color-border)] bg-[var(--color-bg-primary)] hover:border-[var(--color-accent)]'
      }`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h3 className="truncate text-sm font-semibold text-[var(--color-text-primary)]">{definition.name}</h3>
            {definition.color && <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: definition.color }} />}
          </div>
          <div className="mt-1 flex flex-wrap gap-1">
            <Tag size="small" color={definition.scope === 'project' ? 'blue' : 'mauve'}>{definition.scope}</Tag>
            <Tag size="small" color="transparent">{definition.permissionMode}</Tag>
            <Tag size="small" color="transparent">{invocationCount} 次调用</Tag>
          </div>
        </div>
        <ChevronRight size={16} className={`mt-1 shrink-0 text-[var(--color-text-tertiary)] transition-transform ${selected ? 'translate-x-0.5 text-[var(--color-accent)]' : ''}`} />
      </div>

      <p className="mt-2 line-clamp-2 text-xs leading-5 text-[var(--color-text-secondary)]">
        {definition.description || '暂无描述'}
      </p>

      <div className="mt-3 grid gap-2 text-xs">
        <InfoBlock
          icon={ShieldCheck}
          label="工具"
          values={allowedTools}
          color={definition.tools?.length ? 'blue' : 'green'}
        />
        {(definition.skills.length > 0 || definition.mcpServers.length > 0 || hasMetadata) && (
          <div className="flex flex-wrap gap-1">
            {definition.skills.length > 0 && <Tag size="small" color="blue">Skills {definition.skills.length}</Tag>}
            {definition.mcpServers.length > 0 && <Tag size="small" color="mauve">MCP {definition.mcpServers.length}</Tag>}
            {hasMetadata && <Tag size="small" color="transparent">metadata</Tag>}
          </div>
        )}
      </div>
    </button>
  );
}

function InfoBlock({
  icon: Icon,
  label,
  values,
  color,
}: {
  icon: typeof ShieldCheck;
  label: string;
  values: string[];
  color: string;
}) {
  return (
    <div>
      <div className="mb-1 flex items-center gap-1.5 font-medium text-[var(--color-text-secondary)]">
        <Icon size={13} />
        {label}
      </div>
      <div className="flex flex-wrap gap-1">
        {values.slice(0, 8).map((value) => (
          <Tag key={value} size="small" color={color}>{value}</Tag>
        ))}
        {values.length > 8 && <Tag size="small" color="transparent">+{values.length - 8}</Tag>}
      </div>
    </div>
  );
}
