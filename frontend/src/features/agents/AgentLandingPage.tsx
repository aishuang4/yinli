/**
 * AgentLandingPage — 智能体管理第一层入口页。
 *
 * 只展示对象列表和主要入口，具体智能体设置在二级详情页完成。
 */

import { Bot, HelpCircle, List, Plus, Trash2, WandSparkles } from 'lucide-react';
import { Button } from '@agentscope-ai/design';
import {
  WorkbenchEmptyPanel,
  WorkbenchList,
  WorkbenchListPage,
  WorkbenchListRow,
  WorkbenchLoadingPanel,
  WorkbenchSection,
} from '../../components/workbench';
import type { Agent } from '../../types/agent';

interface AgentLandingPageProps {
  agents: Agent[];
  loading: boolean;
  onCreateAgent: () => void;
  onBuilderOpen: () => void;
  onSelectAgent: (agentId: string) => void;
  onDeleteAgent: (agentId: string) => void;
}

export function AgentLandingPage({
  agents,
  loading,
  onCreateAgent,
  onBuilderOpen,
  onSelectAgent,
  onDeleteAgent,
}: AgentLandingPageProps) {
  return (
    <WorkbenchListPage
      title="智能体"
      icon={<HelpCircle size={18} />}
      actions={(
        <>
          <Button icon={<WandSparkles size={15} />} onClick={onBuilderOpen}>
            智能创建
          </Button>
          <Button type="primary" icon={<Plus size={15} />} onClick={onCreateAgent}>
            创建
          </Button>
        </>
      )}
    >

      <WorkbenchSection title="自定义智能体">
        {loading ? (
          <WorkbenchLoadingPanel />
        ) : agents.length === 0 ? (
          <WorkbenchEmptyPanel onClick={onCreateAgent}>
            暂无自定义智能体 <span className="text-[var(--color-text-primary)]">创建</span>
          </WorkbenchEmptyPanel>
        ) : (
          <WorkbenchList>
            {agents.map((agent, index) => (
              <AgentListRow
                key={agent.id}
                agent={agent}
                last={index === agents.length - 1}
                onSelect={() => onSelectAgent(agent.id)}
                onDelete={() => onDeleteAgent(agent.id)}
              />
            ))}
          </WorkbenchList>
        )}
      </WorkbenchSection>
    </WorkbenchListPage>
  );
}

function AgentListRow({
  agent,
  last,
  onSelect,
  onDelete,
}: {
  agent: Agent;
  last: boolean;
  onSelect: () => void;
  onDelete: () => void;
}) {
  return (
    <WorkbenchListRow
      icon={<AgentIcon agent={agent} />}
      title={agent.name}
      subtitle={agent.modelConfig.primary}
      description={agent.description || '配置模型、工具和执行边界'}
      last={last}
      onSelect={onSelect}
      trailing={(
        <>
          <button
            type="button"
            onClick={onSelect}
            className="icon-button h-8 w-8 rounded-md text-[var(--color-text-tertiary)] transition hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]"
            aria-label={`打开 ${agent.name} 设置`}
          >
            <List size={17} />
          </button>
          <button
            type="button"
            onClick={onDelete}
            className="icon-button h-8 w-8 rounded-md text-[var(--color-text-tertiary)] opacity-0 transition hover:bg-[var(--color-error-bg)] hover:text-[var(--color-error)] group-hover:opacity-100"
            aria-label={`删除 ${agent.name}`}
          >
            <Trash2 size={15} />
          </button>
        </>
      )}
    />
  );
}

function AgentIcon({ agent }: { agent: Agent }) {
  return (
    <span className="flex h-10 w-10 shrink-0 items-center justify-center overflow-hidden rounded-lg bg-black text-[var(--color-accent)]">
      {agent.avatar ? (
        <img src={agent.avatar} alt={agent.name} className="h-full w-full object-cover" />
      ) : (
        <Bot size={20} strokeWidth={1.8} />
      )}
    </span>
  );
}
