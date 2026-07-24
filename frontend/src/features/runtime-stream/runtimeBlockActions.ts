/** Runtime Block 命令上下文；命令结果只提交后端，不直接修改展示树。 */

import { createContext } from 'react';

export interface RuntimeBlockActions {
  /** 仅用于构造当前会话产物的受控下载地址。 */
  sessionId?: string;
  onResolveInteraction?: (
    interactionId: string,
    action: 'approve' | 'reject',
    externalResults?: Record<string, string>,
    rememberApproval?: boolean,
  ) => Promise<void>;
}

export const RuntimeBlockActionsContext = createContext<RuntimeBlockActions | null>(null);
