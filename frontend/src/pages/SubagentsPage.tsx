/**
 * SubagentsPage — Subagent 文件定义与委托审计页面。
 *
 * Subagent 由 Markdown + YAML frontmatter 定义，本页只展示已加载定义和运行记录。
 */

import { useEffect, useMemo, useState } from 'react';
import { Bot, FileText, History, List, RefreshCw, Search, ShieldCheck } from 'lucide-react';
import { Button, Tag } from '@agentscope-ai/design';
import { TextField } from '../components/form';
import {
  WorkbenchEmptyPanel,
  WorkbenchDetailShell,
  WorkbenchList,
  WorkbenchListIcon,
  WorkbenchListPage,
  WorkbenchListRow,
  WorkbenchLoadingPanel,
  WorkbenchSection,
} from '../components/workbench';
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
    if (!selectedName) {
      return;
    }
    if (!definitions.some((definition) => definition.name === selectedName)) {
      setSelectedName('');
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

  if (selectedDefinition) {
    return (
      <WorkbenchDetailShell
        title={selectedDefinition.name}
        subtitle={selectedDefinition.description || '暂无描述'}
        onBack={() => setSelectedName('')}
        actions={(
          <>
            <Tag size="small" color={selectedDefinition.scope === 'project' ? 'blue' : 'mauve'}>{selectedDefinition.scope}</Tag>
            <Tag size="small" color="transparent">{selectedDefinition.permissionMode}</Tag>
            <Tag size="small" color={selectedDefinition.model === 'inherit' ? 'green' : 'orange'}>{selectedDefinition.model || 'inherit'}</Tag>
            <Tag size="small" color="transparent">{invocationCounts[selectedDefinition.name] || 0} 次调用</Tag>
          </>
        )}
      >
        <section className="grid h-full min-h-0 grid-rows-[auto_1fr] overflow-hidden">
          <div className="border-b border-[var(--color-border)] bg-[var(--color-bg-primary)] px-5 py-3">
            <div className="flex gap-2">
              <DetailTabButton
                active={activeTab === 'definition'}
                icon={<FileText size={14} />}
                label="定义详情"
                onClick={() => setActiveTab('definition')}
              />
              <DetailTabButton
                active={activeTab === 'history'}
                icon={<History size={14} />}
                label="调用历史"
                onClick={() => setActiveTab('history')}
              />
            </div>
          </div>
          <div className="min-h-0 overflow-y-auto p-5">
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
        </section>
      </WorkbenchDetailShell>
    );
  }

  return (
    <WorkbenchListPage
      title="Subagents"
      icon={<Bot size={18} />}
      actions={(
        <>
          <Tag size="small" color="blue">{definitions.length} 个定义</Tag>
          <Tag size="small" color="green">{completedCount} 次完成</Tag>
          {failedCount > 0 && <Tag size="small" color="red">{failedCount} 次失败</Tag>}
          <Tag size="small" color="transparent">fp {compactHash(fingerprint)}</Tag>
          <Button icon={<RefreshCw size={14} />} onClick={refresh} loading={loading}>
            刷新
          </Button>
        </>
      )}
    >
      <div className="mb-5">
        <TextField
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="搜索 Subagent、工具、任务或结果"
          prefix={<Search size={14} />}
          allowClear
        />
      </div>

      {(errors.length > 0 || warnings.length > 0) && (
        <WorkbenchSection title="加载提示">
          <div className="grid gap-2">
            {errors.map((item) => (
              <div key={`${item.path}-${item.message}`} className="rounded-xl border border-[var(--color-error)] bg-[var(--color-error-bg)] px-4 py-3 text-sm text-[var(--color-error)]">
                <div className="font-medium">{item.path}</div>
                <div className="mt-1">{item.message}</div>
              </div>
            ))}
            {warnings.map((item) => (
              <div key={item} className="rounded-xl border border-[var(--color-warning)] bg-[var(--color-warning-bg)] px-4 py-3 text-sm text-[var(--color-warning)]">
                <div>{item}</div>
              </div>
            ))}
          </div>
        </WorkbenchSection>
      )}

      <WorkbenchSection title="Subagent 定义" compact>
        {error ? (
          <WorkbenchEmptyPanel>
            <span className="text-[var(--color-error)]">{error}</span>
          </WorkbenchEmptyPanel>
        ) : loading ? (
          <WorkbenchLoadingPanel tip="加载 Subagent 数据..." />
        ) : filteredDefinitions.length === 0 ? (
          <WorkbenchEmptyPanel>暂无匹配的 Subagent 定义</WorkbenchEmptyPanel>
        ) : (
          <WorkbenchList>
            {filteredDefinitions.map((definition, index) => (
              <SubagentDefinitionRow
                key={`${definition.scope}-${definition.name}`}
                definition={definition}
                invocationCount={invocationCounts[definition.name] || 0}
                last={index === filteredDefinitions.length - 1}
                onSelect={() => {
                  setSelectedName(definition.name);
                  setActiveTab('definition');
                }}
              />
            ))}
          </WorkbenchList>
        )}
      </WorkbenchSection>
    </WorkbenchListPage>
  );
}

function SubagentDefinitionRow({
  definition,
  invocationCount,
  last,
  onSelect,
}: {
  definition: SubagentDefinition;
  invocationCount: number;
  last: boolean;
  onSelect: () => void;
}) {
  return (
    <WorkbenchListRow
      icon={(
        <WorkbenchListIcon>
          <Bot size={20} strokeWidth={1.8} />
        </WorkbenchListIcon>
      )}
      title={definition.name}
      subtitle={definition.path}
      description={definition.description || '暂无描述'}
      last={last}
      layout="columns"
      onSelect={onSelect}
      meta={(
        <>
          <Tag size="small" color={definition.scope === 'project' ? 'blue' : 'mauve'}>{definition.scope}</Tag>
          <Tag size="small" color="transparent">{invocationCount} 次调用</Tag>
        </>
      )}
      trailing={(
        <button
          type="button"
          onClick={onSelect}
          className="icon-button h-8 w-8 rounded-md text-[var(--color-text-tertiary)] transition hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]"
          aria-label={`打开 ${definition.name} 详情`}
        >
          <List size={17} />
        </button>
      )}
    />
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
            <TagGroup title="MCP Servers（已解析，当前未执行）" values={definition.mcpServers.map((item) => JSON.stringify(item))} color="mauve" empty="未配置" />
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
