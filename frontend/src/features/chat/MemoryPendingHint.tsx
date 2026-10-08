/** 对话结束后提示：用户 / 全局记忆还在待确认，采纳前不会生效。 */

import { useEffect, useState } from 'react';
import { ClipboardCheck } from 'lucide-react';
import { useMemory } from '../../hooks/useMemory';
import { isViewEnabled } from '../../stores/productFeatures';
import { useAppStore } from '../../stores/appStore';
import { useProductFeaturesStore } from '../../stores/productFeaturesStore';

export function MemoryPendingHint({ isStreaming }: { isStreaming: boolean }) {
  const memoryEnabled = useProductFeaturesStore((state) => isViewEnabled(state.features, 'memory'));
  const setView = useAppStore((state) => state.setView);
  const setWorkspaceMode = useAppStore((state) => state.setWorkspaceMode);
  const { loadMemoryCandidates } = useMemory();
  const [pendingCount, setPendingCount] = useState(0);

  useEffect(() => {
    if (!memoryEnabled || isStreaming) return undefined;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      void loadMemoryCandidates('pending')
        .then((data) => {
          if (!cancelled) setPendingCount(data.pendingCount);
        })
        .catch(() => {
          if (!cancelled) setPendingCount(0);
        });
    }, 1600);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [isStreaming, loadMemoryCandidates, memoryEnabled]);

  if (!memoryEnabled || pendingCount <= 0) return null;

  return (
    <button
      type="button"
      className="mx-auto mb-2 flex w-full max-w-3xl items-center gap-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-3 py-2 text-left text-xs text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]"
      onClick={() => {
        setWorkspaceMode('workstation');
        setView('memory');
      }}
    >
      <ClipboardCheck size={14} className="shrink-0 text-[var(--color-accent)]" />
      <span>
        有 {pendingCount} 条用户或全局记忆还没采纳。不确认就不会写入，下次对话也不会用上。
      </span>
    </button>
  );
}
