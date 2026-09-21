/** DisplaySnapshot 历史未关闭 Block 应收成已中断。 */

import { describe, expect, it } from 'vitest';
import { abandonOpenBlocks, emptyDisplaySnapshot } from './protocol';

describe('abandonOpenBlocks', () => {
  it('把仍 open 的 Block 收成 cancelled，已关闭的保持不变', () => {
    const snapshot = emptyDisplaySnapshot();
    snapshot.blocksById = {
      bash: { id: 'bash', kind: 'tool', dataVersion: 1, phase: 'open', data: { name: 'Bash' } },
      fetch: { id: 'fetch', kind: 'tool', dataVersion: 1, phase: 'closed', outcome: 'failed', data: { name: 'WebFetch' } },
    };
    snapshot.order = ['bash', 'fetch'];
    const next = abandonOpenBlocks(snapshot);
    expect(next.blocksById.bash.phase).toBe('closed');
    expect(next.blocksById.bash.outcome).toBe('cancelled');
    expect(next.blocksById.fetch.outcome).toBe('failed');
    expect(next.streamDone).toBe(true);
  });

  it('没有 open Block 时返回同一引用', () => {
    const snapshot = emptyDisplaySnapshot();
    expect(abandonOpenBlocks(snapshot)).toBe(snapshot);
  });
});
