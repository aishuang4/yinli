/**
 * McpToolSelectionModal — Agent MCP 工具选择弹窗。
 */

import { useMemo } from 'react';
import { Checkbox, Modal } from '@agentscope-ai/design';
import type { McpServer } from '../../../types/mcp';

type McpToolSelectionModalProps = {
  open: boolean;
  servers: McpServer[];
  selectedToolIds: string[];
  onSelectedToolIdsChange: (toolIds: string[]) => void;
  onClose: () => void;
};

export function McpToolSelectionModal({
  open,
  servers,
  selectedToolIds,
  onSelectedToolIdsChange,
  onClose,
}: McpToolSelectionModalProps) {
  const selectedToolSet = useMemo(() => new Set(selectedToolIds), [selectedToolIds]);

  function toggleServer(server: McpServer, checked: boolean) {
    const serverToolIds = server.tools.map((tool) => tool.id);
    if (checked) {
      onSelectedToolIdsChange(Array.from(new Set([...selectedToolIds, ...serverToolIds])));
      return;
    }
    onSelectedToolIdsChange(selectedToolIds.filter((toolId) => !serverToolIds.includes(toolId)));
  }

  function toggleTool(toolId: string, checked: boolean) {
    if (checked) {
      onSelectedToolIdsChange(Array.from(new Set([...selectedToolIds, toolId])));
      return;
    }
    onSelectedToolIdsChange(selectedToolIds.filter((id) => id !== toolId));
  }

  return (
    <Modal
      title="选择 MCP 工具"
      open={open}
      onCancel={onClose}
      onOk={onClose}
      okText="完成"
      cancelText="关闭"
      width={720}
    >
      {servers.length === 0 ? (
        <div className="py-8 text-center text-sm text-[var(--color-text-tertiary)]">
          暂无已安装并启用的 MCP Server。
        </div>
      ) : (
        <div className="max-h-[520px] space-y-3 overflow-y-auto pr-1">
          {servers.map((server) => {
            const serverToolIds = server.tools.map((tool) => tool.id);
            const selectedCount = serverToolIds.filter((toolId) => selectedToolSet.has(toolId)).length;
            const allSelected = selectedCount === serverToolIds.length;
            const partiallySelected = selectedCount > 0 && !allSelected;

            return (
              <div
                key={server.id}
                className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-primary)]"
              >
                <div className="flex items-start justify-between gap-3 border-b border-[var(--color-border)] px-4 py-3">
                  <Checkbox
                    checked={allSelected}
                    indeterminate={partiallySelected}
                    onChange={(event) => toggleServer(server, event.target.checked)}
                  >
                    <span className="font-medium text-[var(--color-text-primary)]">
                      {server.name}
                    </span>
                  </Checkbox>
                  <div className="shrink-0 text-xs text-[var(--color-text-tertiary)]">
                    {partiallySelected ? `${selectedCount}/${server.tools.length}` : `${server.tools.length} 个工具`}
                  </div>
                </div>
                <div className="px-4 py-3">
                  <div className="mb-3 line-clamp-2 text-xs text-[var(--color-text-secondary)]">
                    {server.description}
                  </div>
                  <div className="space-y-2">
                    {server.tools.map((tool) => (
                      <label
                        key={tool.id}
                        className="flex cursor-pointer items-start gap-2 rounded-md px-2 py-1.5 hover:bg-[var(--color-bg-hover)]"
                      >
                        <Checkbox
                          checked={selectedToolSet.has(tool.id)}
                          onChange={(event) => toggleTool(tool.id, event.target.checked)}
                        />
                        <span className="min-w-0">
                          <span className="block text-sm text-[var(--color-text-primary)]">
                            {tool.name}
                          </span>
                          <span className="line-clamp-2 text-xs text-[var(--color-text-tertiary)]">
                            {tool.description || tool.id}
                          </span>
                        </span>
                      </label>
                    ))}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </Modal>
  );
}
