/** 按 kind 选择 renderer；未知 kind 只显示公共元数据，不渲染 data。 */

import type { DisplayBlock } from './protocol';
import type { BlockRegistry } from './registry';

function GenericBlock({ block }: { block: DisplayBlock }) {
  return (
    <div className="border-l-2 border-[var(--color-border)] px-3 py-2 text-xs text-[var(--color-text-tertiary)]" data-block-kind="unknown" data-unknown-kind={block.kind}>
      <span>{block.kind}</span>
      <span className="ml-2">{block.outcome ?? block.phase}</span>
    </div>
  );
}

export function RuntimeBlockRenderer({ block, registry }: { block: DisplayBlock; registry: BlockRegistry }) {
  const Renderer = registry.get(block.kind)?.renderer;
  return Renderer ? <Renderer block={block} /> : <GenericBlock block={block} />;
}
