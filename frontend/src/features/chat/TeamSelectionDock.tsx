import { useState } from 'react';
import { Loader2 } from 'lucide-react';
import { Button } from '@agentscope-ai/design';
import type { Agent } from '../../types/agent';
import type { TeamMember } from '../../types/team';
import type { PendingTeamSelection } from './types';

export function TeamSelectionDock({
  pending,
  selecting,
  agents,
  members,
  onConfirm,
  onCancel,
}: {
  pending: PendingTeamSelection | null;
  selecting: boolean;
  agents: Agent[];
  members: TeamMember[];
  onConfirm: (workerIds: string[]) => void;
  onCancel: () => void;
}) {
  const [showDetails, setShowDetails] = useState(false);
  const workerMembers = members.filter((member) => member.role === 'worker');
  const allWorkerIds = workerMembers.map((member) => member.agentId);
  const selected = new Set(pending?.selectedAgentIds || []);
  const selectedNames = workerMembers
    .filter((member) => selected.has(member.agentId))
    .map((member) => agents.find((agent) => agent.id === member.agentId)?.name || member.name);

  return (
    <div className="mx-auto mb-3 max-w-4xl rounded-xl bg-[var(--color-bg-secondary)] p-3 shadow-md transition-all duration-300 ease-out">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-sm font-medium text-[var(--color-text-primary)]">
            {selecting && <Loader2 size={14} className="animate-spin text-[var(--color-accent)]" />}
            <span>{selecting ? '正在准备 Team 协作' : '确认 Team 协作'}</span>
          </div>
          <div className="mt-1 text-xs text-[var(--color-text-tertiary)]">
            {selecting
              ? '系统正在根据当前任务准备相关 Worker。'
              : selectedNames.length > 0
                ? `建议使用 ${selectedNames.length} 个 Worker，可直接确认或调整为全部/仅 Leader。`
                : '建议仅由 Leader 处理，不启动 Worker。'}
          </div>
        </div>
        {!selecting && (
          <div className="flex shrink-0 items-center gap-1">
            <Button size="small" type="text" onClick={() => setShowDetails((value) => !value)}>
              {showDetails ? '收起' : '查看建议'}
            </Button>
            <button
              type="button"
              onClick={onCancel}
              className="rounded-md px-2 py-1 text-xs text-[var(--color-text-tertiary)] hover:bg-[var(--color-bg-hover)]"
            >
              取消
            </button>
          </div>
        )}
      </div>
      {!selecting && pending && showDetails && (
        <div className="mt-2 rounded-md bg-[var(--color-bg-primary)] px-3 py-2 text-xs text-[var(--color-text-secondary)]">
          <div className="text-[var(--color-text-tertiary)]">{pending.reason || '模型已给出 Worker 建议。'}</div>
          <div className="mt-1">
            {selectedNames.length > 0 ? selectedNames.join('、') : '仅 Leader'}
          </div>
        </div>
      )}
      {!selecting && pending && (
        <div className="mt-3 flex flex-wrap justify-end gap-2">
          <Button onClick={() => onConfirm([])}>
            仅 Leader
          </Button>
          <Button onClick={() => onConfirm(allWorkerIds)}>
            全部 Worker
          </Button>
          <Button type="primary" onClick={() => onConfirm(pending.selectedAgentIds)}>
            确认协作
          </Button>
        </div>
      )}
    </div>
  );
}
