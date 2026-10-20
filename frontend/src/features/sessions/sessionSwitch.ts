/** Session 切換計畫。進行中的 Run 不切；允許時先清投影、附件和訂閱。 */

export interface SessionSwitchPlan {
  allowed: boolean;
  reason: '' | 'streaming' | 'same-session';
  clear: Array<'subscription' | 'projection' | 'attachments' | 'preview' | 'studio'>;
}

export function planSessionSwitch(input: {
  streaming: boolean;
  currentId: string | null;
  nextId: string;
}): SessionSwitchPlan {
  if (input.streaming) {
    return { allowed: false, reason: 'streaming', clear: [] };
  }
  if (input.currentId === input.nextId) {
    return { allowed: false, reason: 'same-session', clear: [] };
  }
  return {
    allowed: true,
    reason: '',
    clear: ['subscription', 'projection', 'attachments', 'preview', 'studio'],
  };
}
