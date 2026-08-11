/** DisplaySnapshot V4 的唯一 Chat 树形渲染入口。 */

import { useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { RuntimeBlockRenderer } from './BlockRenderer';
import { coreBlockRegistry } from './coreModules';
import type { DisplaySnapshot } from './protocol';
import { RuntimeBlockActionsContext, type RuntimeBlockActions } from './runtimeBlockActions';
import { RuntimeAnimationContext, type RuntimeAnimationReporter } from './RuntimeAnimationContext';
import { RuntimeProcessGroup } from './RuntimeProcessGroup';
import { TeamConsole } from './TeamBlockViews';
import { TeamRuntimeContext } from './TeamRuntimeContext';

export function RuntimeBlockTree({
  snapshot,
  actions,
  sessionId,
  onAnswerAnimationChange,
}: {
  snapshot: DisplaySnapshot;
  actions?: RuntimeBlockActions;
  sessionId?: string;
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
    const processChildren = block.kind === 'message'
      ? children.filter((childId) => {
          const child = snapshot.blocksById[childId];
          return child && (coreBlockRegistry.get(child.kind)?.presentation || 'process') === 'process';
        })
      : [];
    const supplementChildren = block.kind === 'message'
      ? children.filter((childId) => {
          const child = snapshot.blocksById[childId];
          return child && coreBlockRegistry.get(child.kind)?.presentation === 'supplement';
        })
      : [];
    const groupedChildSet = new Set([...processChildren, ...supplementChildren]);
    const mainChildren = block.kind === 'message'
      ? children.filter((childId) => !groupedChildSet.has(childId))
      : children;
    const hasFinalAnswer = block.kind === 'message' && children.some((childId) => {
      const child = snapshot.blocksById[childId];
      return child && coreBlockRegistry.get(child.kind)?.presentation === 'answer';
    });
    return (
      <div key={id} data-runtime-block-id={id} data-runtime-block-kind={block.kind}>
        <RuntimeBlockRenderer block={block} registry={coreBlockRegistry} />
        {children.length > 0 && block.kind !== 'team' ? (
          <div className={block.kind === 'message' ? 'space-y-2' : 'ml-4 mt-2 space-y-2'}>
            {processChildren.length ? (
              <RuntimeProcessGroup
                key={`process:${id}`}
                blocks={processChildren.map((processId) => snapshot.blocksById[processId])}
                hasFinalAnswer={hasFinalAnswer}
              >
                {processChildren.map(renderBranch)}
              </RuntimeProcessGroup>
            ) : null}
            {mainChildren.map(renderBranch)}
            {supplementChildren.map(renderBranch)}
          </div>
        ) : null}
      </div>
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
