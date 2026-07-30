/**
 * TraceLandingPage — 运行轨迹第一层列表页。
 *
 * 只展示历史 Run 列表；点击后进入二级详情页查看消息、事件和工具调用。
 */

import { Activity, List, RefreshCw } from 'lucide-react';
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
import type { TraceSummary } from './traceTypes';

interface TraceLandingPageProps {
  traces: TraceSummary[];
  loading: boolean;
  error: string | null;
  onSelect: (traceId: string) => void;
  onRefresh: () => void;
}

export function TraceLandingPage({
  traces,
  loading,
  error,
  onSelect,
  onRefresh,
}: TraceLandingPageProps) {
  return (
    <WorkbenchListPage
      title="运行轨迹"
      icon={<Activity size={18} />}
      actions={(
        <>
          <Tag size="small" color="blue">{traces.length} 条历史运行</Tag>
          <Button icon={<RefreshCw size={14} />} onClick={onRefresh} loading={loading}>
            刷新
          </Button>
        </>
      )}
    >
      <WorkbenchSection title="Trace" compact>
        {error ? (
          <WorkbenchEmptyPanel>
            <span className="text-[var(--color-error)]">{error}</span>
          </WorkbenchEmptyPanel>
        ) : loading ? (
          <WorkbenchLoadingPanel tip="加载运行轨迹..." />
        ) : traces.length === 0 ? (
          <WorkbenchEmptyPanel>暂无运行轨迹</WorkbenchEmptyPanel>
        ) : (
          <WorkbenchList>
            {traces.map((trace, index) => (
              <TraceListRow
                key={trace.id}
                trace={trace}
                last={index === traces.length - 1}
                onSelect={() => onSelect(trace.id)}
              />
            ))}
          </WorkbenchList>
        )}
      </WorkbenchSection>
    </WorkbenchListPage>
  );
}

function TraceListRow({
  trace,
  last,
  onSelect,
}: {
  trace: TraceSummary;
  last: boolean;
  onSelect: () => void;
}) {
  return (
    <WorkbenchListRow
      icon={(
        <WorkbenchListIcon>
          <Activity size={20} strokeWidth={1.8} />
        </WorkbenchListIcon>
      )}
      title={trace.title}
      subtitle={trace.mode === 'team' ? `${trace.agentName || 'Agent'} · Team Leader` : trace.agentName || 'Agent'}
      description={trace.updatedAt || trace.createdAt}
      last={last}
      layout="columns"
      onSelect={onSelect}
      meta={(
        <>
          <Tag size="small" color={trace.status === 'failed' ? 'red' : trace.status === 'waiting' ? 'orange' : 'green'}>
            {trace.status}
          </Tag>
          <Tag size="small" color="transparent">{trace.messageCount} 消息</Tag>
          <Tag size="small" color="transparent">{trace.toolCallCount} 工具</Tag>
          {trace.memberCount > 0 ? <Tag size="small" color="transparent">{trace.memberCount} Member</Tag> : null}
        </>
      )}
      trailing={(
        <button
          type="button"
          onClick={onSelect}
          className="icon-button h-8 w-8 rounded-md text-[var(--color-text-tertiary)] transition hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]"
          aria-label={`打开 ${trace.title} 详情`}
        >
          <List size={17} />
        </button>
      )}
    />
  );
}
