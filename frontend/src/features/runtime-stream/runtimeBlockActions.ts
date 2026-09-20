/** Runtime Block 命令上下文；命令结果只提交后端，不直接修改展示树。 */

import { createContext } from 'react';
import type { FilePreviewTarget } from '../file-preview/filePreview';

export interface RuntimeBlockActions {
  /** 仅用于构造当前会话产物的受控下载 / 预览地址。 */
  sessionId?: string;
  /** 当前右侧预览打开的会话相对路径，用于高亮产物行。 */
  previewPath?: string;
  onOpenPreview?: (target: FilePreviewTarget) => void;
  onResolveInteraction?: (
    interactionId: string,
    action: 'approve' | 'reject',
    externalResults?: Record<string, string>,
    rememberApproval?: boolean,
  ) => Promise<void>;
}

export const RuntimeBlockActionsContext = createContext<RuntimeBlockActions | null>(null);
