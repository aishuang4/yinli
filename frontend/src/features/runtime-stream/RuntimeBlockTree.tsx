/** DisplaySnapshot V4 的唯一 Chat 树形渲染入口。 */

import { useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { partitionMessageChildren } from './answerPartition';
import { RuntimeBlockRenderer } from './BlockRenderer';
import { ProcessTextBlockView } from './CoreBlockViews';
import { coreBlockRegistry } from './coreModules';
import {
  CollapsibleTrace,
  LiveThinking,
  MessageStatus,
  ToolCallRow,
} from './TraceRows';
import {
  messageStatusText,
  segmentTrace,
  traceBlockLabel,
  type TraceSegment,
} from './processTrace';
import type { DisplaySnapshot } from './protocol';
import { RuntimeBlockActionsContext, type RuntimeBlockActions } from './runtimeBlockActions';
import { RuntimeAnimationContext, type RuntimeAnimationReporter } from './RuntimeAnimationContext';
import { TeamConsole } from './TeamBlockViews';
import { TeamRuntimeContext } from './TeamRuntimeContext';

export function RuntimeBlockTree({
  snapshot,
  actions,
  sessionId,
  processStopped = false,
  elapsedSeconds = null,
  onAnswerAnimationChange,
}: {
  snapshot: DisplaySnapshot;
  actions?: RuntimeBlockActions;
  sessionId?: string;
  processStopped?: boolean;
  elapsedSeconds?: number | null;
  onAnswerAnimationChange?: RuntimeAnimationReporter;
}) {
  const [openTeamId, setOpenTeamId] = useState<string | null>(null);
  const [openTeamTab, setOpenTeamTab] = useState<'tasks' | 'members' | 'mail'>('tasks');
  const openTeam = (teamBlockId: string, tab: 'tasks' | 'members' | 'mail' = 'tasks') => {
    setOpenTeamId(teamBlockId);
    setOpenTeamTab(tab);
  };
  const childrenByParent = useMemo(() => {
    const result = new Map<string, string[]>();
    for (const id of snapshot.order) {
      const parent = snapshot.blocksById[id]?.parent || '';
      const siblings = result.get(parent) || [];
      siblings.push(id);
      result.set(parent, siblings);
    }
    return result;
  }, [snapshot]);

  const renderBranch = (id: string): React.ReactNode => {
    const block = snapshot.blocksById[id];
    if (!block) return null;
    if (coreBlockRegistry.get(block.kind)?.presentation === 'detached') return null;
    const children = childrenByParent.get(id) || [];
    const partition = block.kind === 'message'
      ? partitionMessageChildren(snapshot, id, children)
      : null;
    const folded = partition?.mode === 'folded';
    const stopped = processStopped || block.outcome === 'cancelled' || block.outcome === 'failed';
    const running = Boolean(partition && block.phase === 'open' && !stopped);
    const trace = partition
      ? segmentTrace(snapshot, folded ? partition.processIds : visibleChildIds(children))
      : [];
    const expandable = Boolean(folded && trace.length);
    return (
      <div key={id} data-runtime-block-id={id} data-runtime-block-kind={block.kind}>
        <RuntimeBlockRenderer block={block} registry={coreBlockRegistry} />
        {children.length > 0 && block.kind !== 'team' ? (
          <div className={block.kind === 'message' ? 'space-y-3' : 'ml-4 mt-2 space-y-2'}>
            {partition ? (
              <MessageStatus
                label={messageStatusText({ running, stopped, seconds: elapsedSeconds })}
                expandable={expandable}
              >
                {expandable ? (
                  <RuntimeAnimationContext.Provider value={null}>
                    {trace.map((segment) => renderTraceSegment(segment, false))}
                  </RuntimeAnimationContext.Provider>
                ) : null}
              </MessageStatus>
            ) : null}
            {folded && partition ? (
              <>
                {partition.answerIds.map(renderBranch)}
                {partition.supplementIds.map(renderBranch)}
              </>
            ) : partition ? (
              trace.map((segment) => renderTraceSegment(segment, true))
            ) : children.map(renderBranch)}
          </div>
        ) : null}
      </div>
    );
  };

  const visibleChildIds = (ids: string[]): string[] => ids.filter((childId) => {
    const child = snapshot.blocksById[childId];
    return Boolean(child && coreBlockRegistry.get(child.kind)?.presentation !== 'detached');
  });

  const renderTraceSegment = (segment: TraceSegment, live: boolean): React.ReactNode => {
    if (segment.type === 'prose') return renderBranch(segment.id);
    if (segment.type === 'thinking') {
      const blocks = segment.ids.flatMap((id) => {
        const thinking = snapshot.blocksById[id];
        return thinking ? [thinking] : [];
      });
      return (
        <LiveThinking key={segment.ids.join(':')} spinning={blocks.some((block) => block.phase === 'open')}>
          {blocks.map((block) => <ProcessTextBlockView key={block.id} block={block} hideLabel />)}
        </LiveThinking>
      );
    }
    if (segment.type === 'activity') {
      const tools = segment.ids.flatMap((id) => {
        const block = snapshot.blocksById[id];
        return block ? [block] : [];
      });
      return (
        <div key={segment.ids.join(':')} className="space-y-1">
          {tools.map((tool) => <ToolCallRow key={tool.id} tool={tool} />)}
        </div>
      );
    }
    const child = snapshot.blocksById[segment.id];
    if (live || (child?.kind === 'approval' && child.phase === 'open')) return renderBranch(segment.id);
    return (
      <CollapsibleTrace key={segment.id} label={traceBlockLabel(child)}>
        {renderBranch(segment.id)}
      </CollapsibleTrace>
    );
  };

  return (
    <RuntimeBlockActionsContext.Provider value={actions || sessionId ? { ...actions, sessionId } : null}>
      <RuntimeAnimationContext.Provider value={onAnswerAnimationChange || null}>
        <TeamRuntimeContext.Provider value={{
          snapshot,
          openTeamId,
          openTeamTab,
          onOpenTeam: openTeam,
          onCloseTeam: () => setOpenTeamId(null),
        }}>
          <div className="space-y-2">{(childrenByParent.get('') || []).map(renderBranch)}</div>
          {openTeamId
            ? createPortal(<TeamConsole snapshot={snapshot} teamBlockId={openTeamId} initialTab={openTeamTab} />, document.body)
            : null}
        </TeamRuntimeContext.Provider>
      </RuntimeAnimationContext.Provider>
    </RuntimeBlockActionsContext.Provider>
  );
}
