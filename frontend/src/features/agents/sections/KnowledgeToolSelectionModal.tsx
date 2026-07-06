/**
 * KnowledgeToolSelectionModal — Agent HTTP 工具选择弹窗。
 */

import { Pencil, Trash2 } from 'lucide-react';
import { Button, Checkbox, Modal, Tag } from '@agentscope-ai/design';
import type { HttpTool } from '../../../types/tool';

type KnowledgeToolSelectionModalProps = {
  open: boolean;
  tools: HttpTool[];
  selectedToolIds: string[];
  onToggleTool: (toolId: string, checked: boolean) => void;
  onEditTool: (tool: HttpTool) => void;
  onDeleteTool: (toolId: string) => void;
  onClose: () => void;
};

export function KnowledgeToolSelectionModal({
  open,
  tools,
  selectedToolIds,
  onToggleTool,
  onEditTool,
  onDeleteTool,
  onClose,
}: KnowledgeToolSelectionModalProps) {
  const selectedToolSet = new Set(selectedToolIds);

  return (
    <Modal
      title="选择 HTTP 工具"
      open={open}
      onCancel={onClose}
      onOk={onClose}
      okText="完成"
      cancelText="关闭"
      width={760}
    >
      {tools.length === 0 ? (
        <div className="py-8 text-center text-sm text-[var(--color-text-tertiary)]">
          暂无 HTTP 工具。请先到工具管理中配置可注入 Agent 的 HTTP 工具。
        </div>
      ) : (
        <div className="max-h-[520px] space-y-2 overflow-y-auto pr-1">
          {tools.map((tool) => (
            <div
              key={tool.id}
              className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-primary)] px-4 py-3"
            >
              <div className="flex items-start justify-between gap-3">
                <Checkbox
                  checked={selectedToolSet.has(tool.id)}
                  disabled={!tool.enabled}
                  onChange={(event) => onToggleTool(tool.id, event.target.checked)}
                >
                  <span className="font-medium text-[var(--color-text-primary)]">
                    {tool.name}
                  </span>
                </Checkbox>
                <div className="flex shrink-0 gap-1">
                  <Button
                    type="text"
                    icon={<Pencil size={14} />}
                    onClick={() => onEditTool(tool)}
                    aria-label="编辑 HTTP 工具"
                  />
                  <Button
                    danger
                    type="text"
                    icon={<Trash2 size={14} />}
                    onClick={() => onDeleteTool(tool.id)}
                    aria-label="删除 HTTP 工具"
                  />
                </div>
              </div>
              <div className="mt-2 line-clamp-2 text-xs text-[var(--color-text-secondary)]">
                {tool.description || tool.parameterInstructions}
              </div>
              <div className="mt-2 flex items-center gap-2 text-xs text-[var(--color-text-tertiary)]">
                <Tag size="small" color={tool.enabled ? 'green' : 'transparent'}>
                  {tool.enabled ? '已启用' : '已停用'}
                </Tag>
                <span className="truncate">{tool.url}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </Modal>
  );
}
