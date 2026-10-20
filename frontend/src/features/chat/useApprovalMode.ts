/** 审核选择按会话保存，新 Chat 默认人工；切 Agent/Team 不参与该状态。 */
import { useState } from 'react';
import { notifyError } from '../../utils/feedback';
import type { ChatSession } from './types';
import type { ApprovalMode } from './approvalMode';

export function useApprovalMode(sessionId: string | null, sessions: ChatSession[]) {
  const [choices, setChoices] = useState<Record<string, ApprovalMode>>({});
  const key = sessionId || 'new';
  const approvalMode = choices[key] || sessions.find(session => session.id === sessionId)?.approvalMode || 'manual';
  const select = async (mode: ApprovalMode) => {
    if (sessionId) {
      try {
        const response = await fetch(`/api/chat/sessions/${encodeURIComponent(sessionId)}/approval-mode`, {
          method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ approvalMode: mode }),
        });
        if (!response.ok) {
          const data = await response.json();
          throw new Error(data.detail || '审核模式保存失败');
        }
      } catch (error) {
        notifyError(error instanceof Error ? error.message : '审核模式保存失败');
        return;
      }
    }
    setChoices(current => ({ ...current, [key]: mode }));
  };
  const sent = (id: string) => setChoices(current => ({ ...current, [id]: approvalMode, new: 'manual' }));
  return { approvalMode, select, sent };
}
