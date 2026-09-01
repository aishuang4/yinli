/** 工作室独立审批条。不走 Chat HITL / ResultModal。 */

import { AlertCircle, Wrench } from 'lucide-react';
import { Button } from '@/components/design';
import type { GroupStudioApproval } from './types';

interface GroupApprovalBarProps {
  approval: GroupStudioApproval;
  memberName: string;
  submitting: boolean;
  onDecide: (decision: 'approve' | 'deny') => void;
}

export function GroupApprovalBar({
  approval,
  memberName,
  submitting,
  onDecide,
}: GroupApprovalBarProps) {
  const names = approval.toolNames.length > 0
    ? approval.toolNames
    : approval.toolCalls.map((item) => String(item.name || 'tool'));

  return (
    <div className="group-studio-approval">
      <div className="group-studio-approval__title">
        <AlertCircle size={14} />
        <span>{memberName} 请求执行工具</span>
      </div>
      <ul className="group-studio-approval__tools">
        {names.map((name) => (
          <li key={name}>
            <Wrench size={12} />
            <span>{name}</span>
          </li>
        ))}
      </ul>
      {approval.argumentsPreview ? (
        <pre className="group-studio-approval__preview">{approval.argumentsPreview}</pre>
      ) : null}
      <div className="group-studio-approval__actions">
        <Button
          size="small"
          danger
          disabled={submitting}
          onClick={() => onDecide('deny')}
        >
          拒绝
        </Button>
        <Button
          size="small"
          type="primary"
          disabled={submitting}
          onClick={() => onDecide('approve')}
        >
          允许
        </Button>
      </div>
    </div>
  );
}
