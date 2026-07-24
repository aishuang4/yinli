/** DisplaySnapshot V4 的唯一 Chat 树形渲染入口。 */

import { useMemo } from 'react';
import { RuntimeBlockRenderer } from './BlockRenderer';
import { coreBlockRegistry } from './coreModules';
import type { DisplaySnapshot } from './protocol';
import { RuntimeBlockActionsContext, type RuntimeBlockActions } from './runtimeBlockActions';
import { RuntimeProcessGroup } from './RuntimeProcessGroup';

export function RuntimeBlockTree({
  snapshot,
  actions,
  sessionId,
}: {
  snapshot: DisplaySnapshot;
  actions?: RuntimeBlockActions;
  sessionId?: string;
}) {
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
        {children.length > 0 ? (
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
      <div className="space-y-2">{(childrenByParent.get('') || []).map(renderBranch)}</div>
    </RuntimeBlockActionsContext.Provider>
  );
}
