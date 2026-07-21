/**
 * SystemToolsPanel — 系统工具二级展示面板。
 *
 * 系统工具由运行时内置，这里只展示能力、风险和默认注入策略。
 */

import { RefreshCw, Settings2 } from 'lucide-react';
import { Button, Tag } from '@agentscope-ai/design';
import {
  WorkbenchEmptyPanel,
  WorkbenchList,
  WorkbenchListIcon,
  WorkbenchListRow,
  WorkbenchLoadingPanel,
  WorkbenchSection,
} from '../../components/workbench';
import { useSystemTools } from '../../hooks/useKnowledgeTools';
import type { SystemTool } from '../../types/tool';

export function SystemToolsPanel() {
  const { tools, loading, error, refresh } = useSystemTools();

  return (
    <section className="h-full min-h-0 overflow-y-auto bg-[var(--color-bg-primary)]">
      <div className="mx-auto w-full max-w-5xl px-5 py-5">
        <div className="mb-5 flex items-center justify-between gap-3">
          <div className="text-sm text-[var(--color-text-secondary)]">
            系统工具由运行时内置，当前用于展示能力、风险和默认注入策略。
          </div>
          <Button icon={<RefreshCw size={14} />} onClick={() => void refresh()} loading={loading}>
            刷新
          </Button>
        </div>

        <WorkbenchSection title="系统工具" compact>
          {error ? (
            <WorkbenchEmptyPanel>
              <span className="text-[var(--color-error)]">{error}</span>
            </WorkbenchEmptyPanel>
          ) : loading ? (
            <WorkbenchLoadingPanel tip="加载系统工具..." />
          ) : tools.length === 0 ? (
            <WorkbenchEmptyPanel>暂无系统工具</WorkbenchEmptyPanel>
          ) : (
            <WorkbenchList>
              {tools.map((tool, index) => (
                <SystemToolRow
                  key={tool.name}
                  tool={tool}
                  last={index === tools.length - 1}
                />
              ))}
            </WorkbenchList>
          )}
        </WorkbenchSection>
      </div>
    </section>
  );
}

function SystemToolRow({
  tool,
  last,
}: {
  tool: SystemTool;
  last: boolean;
}) {
  return (
    <WorkbenchListRow
      icon={(
        <WorkbenchListIcon>
          <Settings2 size={20} strokeWidth={1.8} />
        </WorkbenchListIcon>
      )}
      title={tool.name}
      subtitle={`${tool.category} · ${tool.action}`}
      description={tool.description}
      last={last}
      layout="columns"
      meta={(
        <>
          <Tag size="small" color={tool.risk === 'high' ? 'red' : tool.risk === 'medium' ? 'orange' : 'green'}>
            {tool.risk}
          </Tag>
          <Tag size="small" color={tool.readonly ? 'green' : 'orange'}>{tool.readonly ? '只读' : '可写/执行'}</Tag>
          <Tag size="small" color={tool.defaultEnabled ? 'blue' : 'transparent'}>
            {tool.defaultEnabled ? '默认注入' : '默认关闭'}
          </Tag>
          {tool.platforms?.length ? <Tag size="small" color="transparent">{tool.platforms.join(' / ')}</Tag> : null}
          {tool.platformNotes?.map((note) => <Tag key={note} size="small" color="orange">{note}</Tag>)}
        </>
      )}
    />
  );
}
