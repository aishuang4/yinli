/**
 * AgentLandingPage — 智能体管理第一层入口页。
 *
 * 只展示对象列表和主要入口，具体智能体设置在二级详情页完成。
 */

import { Bot, HelpCircle, List, Plus, Trash2, WandSparkles } from 'lucide-react';
import { Button, Tag } from '@/components/design';
import {
  WorkbenchEmptyPanel,
  WorkbenchList,
  WorkbenchListIcon,
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
  const defaultAgent = agents.find((agent) => agent.id === 'default') || null;
  const chatAgents = agents.filter((agent) => agent.id !== 'default' && agent.role !== 'job');
  const jobAgents = agents.filter((agent) => agent.role === 'job');

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

      <WorkbenchSection title="默认智能体">
        {loading ? (
          <WorkbenchLoadingPanel />
        ) : defaultAgent ? (
          <WorkbenchList>
            <AgentListRow
              agent={defaultAgent}
              last
              onSelect={() => onSelectAgent(defaultAgent.id)}
            />
          </WorkbenchList>
        ) : (
          <WorkbenchEmptyPanel>默认智能体配置暂不可用</WorkbenchEmptyPanel>
        )}
      </WorkbenchSection>

      <WorkbenchSection title="对话智能体">
        {loading ? (
          <WorkbenchLoadingPanel />
        ) : chatAgents.length === 0 ? (
          <WorkbenchEmptyPanel onClick={onCreateAgent}>
            暂无对话智能体 <span className="text-[var(--color-text-primary)]">创建</span>
          </WorkbenchEmptyPanel>
        ) : (
          <WorkbenchList>
            {chatAgents.map((agent, index) => (
              <AgentListRow
                key={agent.id}
                agent={agent}
                last={index === chatAgents.length - 1}
                onSelect={() => onSelectAgent(agent.id)}
                onDelete={agent.systemManaged ? undefined : () => onDeleteAgent(agent.id)}
              />
            ))}
          </WorkbenchList>
        )}
      </WorkbenchSection>
      <WorkbenchSection title="定时任务 Agent">
        {loading ? (
          <WorkbenchLoadingPanel />
        ) : jobAgents.length === 0 ? (
          <WorkbenchEmptyPanel onClick={onCreateAgent}>
            把智能体用途设为「定时任务 Agent」，并配置必注入工具
          </WorkbenchEmptyPanel>
        ) : (
          <WorkbenchList>
            {jobAgents.map((agent, index) => (
              <AgentListRow
                key={agent.id}
                agent={agent}
                last={index === jobAgents.length - 1}
                onSelect={() => onSelectAgent(agent.id)}
                onDelete={agent.systemManaged ? undefined : () => onDeleteAgent(agent.id)}
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
  onDelete?: () => void;
}) {
  return (
    <WorkbenchListRow
      icon={<AgentIcon agent={agent} />}
      title={agent.name}
      subtitle={agent.role === 'job' ? `定时任务 · ${agent.modelConfig.primary}` : agent.modelConfig.primary}
      description={agent.description || (agent.role === 'job' ? '到点执行，Chat 中不可选用' : '配置模型、工具和执行边界')}
      last={last}
      layout="columns"
      onSelect={onSelect}
      trailing={(
        <>
          {agent.systemManaged ? (
            <Tag size="small" color="transparent">系统内置</Tag>
          ) : null}
          <button
            type="button"
            onClick={onSelect}
            className="icon-button h-8 w-8 rounded-md text-[var(--color-text-tertiary)] transition hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]"
            aria-label={`打开 ${agent.name} 设置`}
          >
            <List size={17} />
          </button>
          {onDelete ? (
            <button
              type="button"
              onClick={onDelete}
              className="icon-button h-8 w-8 rounded-md text-[var(--color-text-tertiary)] opacity-0 transition hover:bg-[var(--color-error-bg)] hover:text-[var(--color-error)] group-hover:opacity-100"
              aria-label={`删除 ${agent.name}`}
            >
              <Trash2 size={15} />
            </button>
          ) : null}
        </>
      )}
    />
  );
}

function AgentIcon({ agent }: { agent: Agent }) {
  return (
    <WorkbenchListIcon className="overflow-hidden">
      {agent.avatar ? (
        <img src={agent.avatar} alt={agent.name} className="h-full w-full object-cover" />
      ) : (
        <Bot size={20} strokeWidth={1.8} />
      )}
    </WorkbenchListIcon>
  );
}
