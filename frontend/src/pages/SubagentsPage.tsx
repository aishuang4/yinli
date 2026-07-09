/**
 * SubagentsPage — Subagent 文件定义与委托审计页面。
 *
 * Subagent 由 Markdown + YAML frontmatter 定义，本页只展示已加载定义和运行记录。
 */

import { useEffect, useMemo, useState } from 'react';
import { Bot, FileText, History, RefreshCw, Search, ShieldCheck } from 'lucide-react';
import { Button, Empty, Spinner, Tag } from '@agentscope-ai/design';
import { TextField } from '../components/form';
import { SubagentDefinitionList } from '../features/subagents/SubagentDefinitionList';
import { SubagentInvocationList } from '../features/subagents/SubagentInvocationList';
import { matchText, compactHash } from '../features/subagents/subagentViewUtils';
import { useSubagents } from '../hooks/useSubagents';
import type { SubagentDefinition } from '../types/subagent';

type SubagentDetailTab = 'definition' | 'history';

export function SubagentsPage() {
  const {
    definitions,
    invocations,
    errors,
    warnings,
    fingerprint,
    loading,
    error,
    refresh,
  } = useSubagents();
  const [query, setQuery] = useState('');
  const [selectedName, setSelectedName] = useState('');
  const [activeTab, setActiveTab] = useState<SubagentDetailTab>('definition');

  const filteredDefinitions = useMemo(() => (
    definitions.filter((definition) => matchText(query, [
      definition.name,
      definition.description,
      definition.scope,
      definition.path,
      definition.model,
      definition.permissionMode,
      ...(definition.tools || []),
      ...definition.disallowedTools,
      ...definition.skills,
    ]))
  ), [definitions, query]);

  const invocationCounts = useMemo(() => (
    invocations.reduce<Record<string, number>>((acc, invocation) => {
      acc[invocation.subagentName] = (acc[invocation.subagentName] || 0) + 1;
      return acc;
    }, {})
  ), [invocations]);

  useEffect(() => {
    if (definitions.length === 0) {
      setSelectedName('');
      return;
    }
    if (!definitions.some((definition) => definition.name === selectedName)) {
      setSelectedName(definitions[0].name);
    }
  }, [definitions, selectedName]);

  const selectedDefinition = useMemo(() => (
    definitions.find((definition) => definition.name === selectedName) || null
  ), [definitions, selectedName]);

  const selectedInvocations = useMemo(() => (
    invocations
      .filter((invocation) => invocation.subagentName === selectedName)
      .filter((invocation) => matchText(query, [
      invocation.subagentName,
      invocation.parentAgentName,
      invocation.status,
      invocation.task,
      invocation.expectedOutput,
      invocation.resultPreview,
      invocation.error,
      invocation.definitionPath,
      ]))
  ), [invocations, query, selectedName]);

  const completedCount = invocations.filter((item) => item.status === 'completed').length;
  const failedCount = invocations.filter((item) => item.status === 'failed').length;

  return (
    <div className="grid h-full min-h-0 grid-rows-[auto_auto_1fr] overflow-hidden bg-[var(--color-bg-primary)]">
      <header className="border-b border-[var(--color-border)] px-5 py-4">
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2">
              <Bot size={20} color="var(--color-accent)" />
              <h1 className="text-lg font-semibold text-[var(--color-text-primary)]">Subagents</h1>
            </div>
            <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
              查看文件定义、加载状态和最近的 DelegateTask 委托记录。
            </p>
          </div>
          <div className="flex flex-wrap items-center justify-end gap-2 text-sm text-[var(--color-text-secondary)]">
            <Tag size="small" color="blue">{definitions.length} 个定义</Tag>
            <Tag size="small" color="green">{completedCount} 次完成</Tag>
            {failedCount > 0 && <Tag size="small" color="red">{failedCount} 次失败</Tag>}
            <Tag size="small" color="transparent">fp {compactHash(fingerprint)}</Tag>
            <Button icon={<RefreshCw size={14} />} onClick={refresh} loading={loading}>
              刷新
            </Button>
          </div>
        </div>
      </header>

      <section className="border-b border-[var(--color-border)] px-5 py-3">
        <TextField
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="搜索 Subagent、工具、任务或结果"
          prefix={<Search size={14} />}
          allowClear
        />
      </section>

      <main className="min-h-0 overflow-hidden px-5 py-4">
        {error ? (
          <div className="rounded-md border border-[var(--color-error)] bg-[var(--color-bg-secondary)] p-4 text-sm text-[var(--color-error)]">
            {error}
          </div>
        ) : loading ? (
          <div className="flex h-full min-h-[420px] items-center justify-center">
            <Spinner tip="加载 Subagent 数据..." />
          </div>
        ) : (
          <div className="grid h-full min-h-0 overflow-hidden rounded-md border border-[var(--color-border)] bg-[var(--color-bg-primary)] xl:grid-cols-[360px_minmax(0,1fr)]">
            <SubagentDefinitionList
              definitions={filteredDefinitions}
              errors={errors}
              warnings={warnings}
              selectedName={selectedName}
              invocationCounts={invocationCounts}
              onSelect={(name) => {
                setSelectedName(name);
                setActiveTab('definition');
              }}
            />
            <section className="grid min-h-0 grid-rows-[auto_1fr] overflow-hidden bg-[var(--color-bg-primary)]">
              {selectedDefinition ? (
                <>
                  <SubagentDetailHeader
                    definition={selectedDefinition}
                    invocationCount={invocationCounts[selectedDefinition.name] || 0}
                    activeTab={activeTab}
                    onTabChange={setActiveTab}
                  />
                  <div className="min-h-0 overflow-y-auto p-4">
                    {activeTab === 'definition' ? (
                      <SubagentDefinitionDetail definition={selectedDefinition} />
                    ) : (
                      <SubagentInvocationList
                        invocations={selectedInvocations}
                        title={`${selectedDefinition.name} 调用历史`}
                        emptyDescription="当前 Subagent 暂无匹配的调用记录"
                      />
                    )}
                  </div>
                </>
              ) : (
                <div className="flex h-full min-h-[420px] items-center justify-center">
                  <Empty type="noData" description="请选择一个 Subagent 定义" />
                </div>
              )}
            </section>
          </div>
        )}
      </main>
    </div>
  );
}

function SubagentDetailHeader({
  definition,
  invocationCount,
  activeTab,
  onTabChange,
}: {
  definition: SubagentDefinition;
  invocationCount: number;
  activeTab: SubagentDetailTab;
  onTabChange: (tab: SubagentDetailTab) => void;
}) {
  return (
    <div className="border-b border-[var(--color-border)] px-5 py-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h2 className="truncate text-base font-semibold text-[var(--color-text-primary)]">{definition.name}</h2>
            {definition.color && <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: definition.color }} />}
          </div>
          <p className="mt-1 max-w-3xl text-sm leading-6 text-[var(--color-text-secondary)]">
            {definition.description || '暂无描述'}
          </p>
        </div>
        <div className="flex flex-wrap gap-1">
          <Tag size="small" color={definition.scope === 'project' ? 'blue' : 'mauve'}>{definition.scope}</Tag>
          <Tag size="small" color="transparent">{definition.permissionMode}</Tag>
          <Tag size="small" color={definition.model === 'inherit' ? 'green' : 'orange'}>{definition.model || 'inherit'}</Tag>
          <Tag size="small" color="transparent">{invocationCount} 次调用</Tag>
        </div>
      </div>

      <div className="mt-4 flex gap-2">
        <DetailTabButton
          active={activeTab === 'definition'}
          icon={<FileText size={14} />}
          label="定义详情"
          onClick={() => onTabChange('definition')}
        />
        <DetailTabButton
          active={activeTab === 'history'}
          icon={<History size={14} />}
          label="调用历史"
          onClick={() => onTabChange('history')}
        />
      </div>
    </div>
  );
}

function DetailTabButton({
  active,
  icon,
  label,
  onClick,
}: {
  active: boolean;
  icon: React.ReactNode;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`inline-flex h-8 items-center gap-1.5 rounded-md border px-3 text-xs font-medium transition-colors ${
        active
          ? 'border-[var(--color-accent)] bg-[var(--color-bg-secondary)] text-[var(--color-text-primary)]'
          : 'border-[var(--color-border)] bg-transparent text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]'
      }`}
    >
      {icon}
      {label}
    </button>
  );
}

function SubagentDefinitionDetail({ definition }: { definition: SubagentDefinition }) {
  const allowedTools = definition.tools?.length ? definition.tools : ['继承主 Agent 可用工具'];
  const metadataEntries = Object.entries(definition.metadata || {});

  return (
    <div className="grid gap-4">
      <section className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-4">
        <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-[var(--color-text-primary)]">
          <ShieldCheck size={16} color="var(--color-accent)" />
          运行策略
        </div>
        <div className="grid gap-3 text-sm md:grid-cols-2 xl:grid-cols-4">
          <DetailField label="权限模式" value={definition.permissionMode || 'default'} />
          <DetailField label="模型" value={definition.model || 'inherit'} />
          <DetailField label="最大轮次" value={`max ${definition.maxTurns}`} />
          <DetailField label="内容指纹" value={compactHash(definition.contentHash)} />
        </div>
      </section>

      <section className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-4">
        <div className="mb-3 text-sm font-semibold text-[var(--color-text-primary)]">工具范围</div>
        <div className="grid gap-4 md:grid-cols-2">
          <TagGroup title="允许工具" values={allowedTools} color={definition.tools?.length ? 'blue' : 'green'} />
          <TagGroup title="拒绝工具" values={definition.disallowedTools} color="orange" empty="无拒绝工具" />
        </div>
      </section>

      <section className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-4">
        <div className="mb-3 text-sm font-semibold text-[var(--color-text-primary)]">来源文件</div>
        <div className="break-all rounded-md bg-[var(--color-bg-primary)] p-3 text-xs leading-6 text-[var(--color-text-secondary)]">
          {definition.path}
        </div>
      </section>

      {(definition.skills.length > 0 || definition.mcpServers.length > 0 || metadataEntries.length > 0) && (
        <section className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-4">
          <div className="mb-3 text-sm font-semibold text-[var(--color-text-primary)]">扩展配置</div>
          <div className="grid gap-4 md:grid-cols-3">
            <TagGroup title="Skills" values={definition.skills} color="blue" empty="未配置" />
            <TagGroup title="MCP Servers" values={definition.mcpServers.map((item) => JSON.stringify(item))} color="mauve" empty="未配置" />
            <TagGroup title="Metadata" values={metadataEntries.map(([key, value]) => `${key}: ${String(value)}`)} color="transparent" empty="无" />
          </div>
        </section>
      )}
    </div>
  );
}

function DetailField({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md bg-[var(--color-bg-primary)] p-3">
      <div className="text-xs text-[var(--color-text-tertiary)]">{label}</div>
      <div className="mt-1 truncate text-sm font-medium text-[var(--color-text-primary)]">{value}</div>
    </div>
  );
}

function TagGroup({
  title,
  values,
  color,
  empty = '暂无',
}: {
  title: string;
  values: string[];
  color: string;
  empty?: string;
}) {
  return (
    <div>
      <div className="mb-2 text-xs font-medium text-[var(--color-text-secondary)]">{title}</div>
      {values.length > 0 ? (
        <div className="flex flex-wrap gap-1.5">
          {values.map((value) => (
            <Tag key={value} size="small" color={color}>{value}</Tag>
          ))}
        </div>
      ) : (
        <div className="text-xs text-[var(--color-text-tertiary)]">{empty}</div>
      )}
    </div>
  );
}
