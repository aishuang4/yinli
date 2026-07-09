/**
 * AgentDetailShell — 智能体二级详情页外壳。
 *
 * 统一提供返回、导出、删除等对象级操作，具体配置表单由 children 注入。
 */

import type { ReactNode } from 'react';
import { Download, Trash2 } from 'lucide-react';
import { Button } from '@agentscope-ai/design';
import { WorkbenchDetailShell } from '../../components/workbench';

interface AgentDetailShellProps {
  title: string;
  subtitle: string;
  children: ReactNode;
  onBack: () => void;
  onExport: () => void;
  onDelete?: () => void;
}

export function AgentDetailShell({
  title,
  subtitle,
  children,
  onBack,
  onExport,
  onDelete,
}: AgentDetailShellProps) {
  return (
    <WorkbenchDetailShell
      title={title}
      subtitle={subtitle}
      onBack={onBack}
      actions={(
        <>
          <Button size="small" icon={<Download size={13} />} onClick={onExport}>
            导出
          </Button>
          {onDelete ? (
            <Button size="small" danger icon={<Trash2 size={13} />} onClick={onDelete}>
              删除
            </Button>
          ) : null}
        </>
      )}
    >
      {children}
    </WorkbenchDetailShell>
  );
}
