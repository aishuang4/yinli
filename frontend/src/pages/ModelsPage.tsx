/**
 * ModelsPage — 供应商连接与模型目录。
 *
 * Chat / Sandbox 只从这里选模型，不在 Agent 配置里改。
 */

import { ModelsManager } from '../features/models/ModelsManager';

export function ModelsPage() {
  return (
    <div className="h-full min-h-0 overflow-hidden bg-[var(--color-bg-primary)]">
      <ModelsManager />
    </div>
  );
}
