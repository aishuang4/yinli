/**
 * AgentToolCapabilitiesSection — Agent 能力与工具配置区。
 */

import { Plus } from 'lucide-react';
import { Button, Tag } from '@agentscope-ai/design';
import { FormField } from '../../../components/form';
import type { McpTool } from '../../../types/mcp';
import type { Skill } from '../../../types/skill';
import type { HttpTool } from '../../../types/tool';

type SquareAddButtonProps = {
  ariaLabel: string;
  disabled?: boolean;
  loading?: boolean;
  onClick: () => void;
};

type CapabilityPickerProps = {
  label: string;
  selectedText: string;
  emptyText: string;
  help: string;
  countText: string;
  loading?: boolean;
  disabled?: boolean;
  tagColor: string;
  selectedItems: Array<{ id: string; name: string }>;
  onOpen: () => void;
};

type AgentToolCapabilitiesSectionProps = {
  selectedMcpTools: McpTool[];
  selectedKnowledgeTools: HttpTool[];
  selectedSkills: Skill[];
  mcpToolCount: number;
  knowledgeToolCount: number;
  skillCount: number;
  mcpServersLoading: boolean;
  knowledgeToolsLoading: boolean;
  skillsLoading: boolean;
  hasEnabledMcpServers: boolean;
  hasEnabledSkills: boolean;
  onOpenMcpSelector: () => void;
  onOpenKnowledgeSelector: () => void;
  onOpenSkillsSelector: () => void;
};

function SquareAddButton({
  ariaLabel,
  disabled = false,
  loading = false,
  onClick,
}: SquareAddButtonProps) {
  return (
    <Button
      type="primary"
      size="small"
      icon={<Plus size={14} />}
      loading={loading}
      disabled={disabled}
      aria-label={ariaLabel}
      title={ariaLabel}
      onClick={onClick}
    />
  );
}

function CapabilityPicker({
  label,
  selectedText,
  emptyText,
  help,
  countText,
  loading = false,
  disabled = false,
  tagColor,
  selectedItems,
  onOpen,
}: CapabilityPickerProps) {
  return (
    <FormField label={label} help={countText}>
      <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-primary)] p-3">
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0 text-sm text-[var(--color-text-secondary)]">
            {selectedItems.length > 0 ? selectedText : emptyText}
          </div>
          <SquareAddButton
            ariaLabel={`选择 ${label}`}
            loading={loading}
            disabled={disabled}
            onClick={onOpen}
          />
        </div>
        {selectedItems.length > 0 ? (
          <div className="mt-3 flex max-h-[72px] flex-wrap gap-2 overflow-hidden">
            {selectedItems.slice(0, 8).map((item) => (
              <Tag key={item.id} size="small" color={tagColor}>
                {item.name}
              </Tag>
            ))}
            {selectedItems.length > 8 && (
              <Tag size="small" color="transparent">
                +{selectedItems.length - 8}
              </Tag>
            )}
          </div>
        ) : (
          <div className="mt-2 text-xs text-[var(--color-text-tertiary)]">
            {help}
          </div>
        )}
      </div>
    </FormField>
  );
}

export function AgentToolCapabilitiesSection({
  selectedMcpTools,
  selectedKnowledgeTools,
  selectedSkills,
  mcpToolCount,
  knowledgeToolCount,
  skillCount,
  mcpServersLoading,
  knowledgeToolsLoading,
  skillsLoading,
  hasEnabledMcpServers,
  hasEnabledSkills,
  onOpenMcpSelector,
  onOpenKnowledgeSelector,
  onOpenSkillsSelector,
}: AgentToolCapabilitiesSectionProps) {
  return (
    <section className="rounded-xl bg-[var(--color-bg-primary)] p-4 shadow-sm">
      <div className="mb-4">
        <h3 className="text-sm font-medium text-[var(--color-text-primary)]">
          能力与工具
        </h3>
        <div className="mt-1 text-xs text-[var(--color-text-tertiary)]">
          默认工具、MCP 和 Skills 会共同决定 Agent 的可执行能力。
        </div>
      </div>
      <div className="space-y-4">
        <FormField label="默认工具（已锁定）">
          <div className="flex flex-wrap gap-2">
            {['Read', 'Write', 'Edit', 'Grep', 'Glob', 'Bash', 'HttpRequest', 'WebFetch'].map((tool) => (
              <Tag key={tool} color="transparent" size="small">
                {tool}
              </Tag>
            ))}
          </div>
        </FormField>

        <CapabilityPicker
          label="MCP 工具"
          selectedText={`已选择 ${selectedMcpTools.length} 个 MCP 工具`}
          emptyText="未选择 MCP 工具"
          help={hasEnabledMcpServers
            ? '可以选择整个 MCP Server，或只选择其中某个工具。'
            : '请先在 MCP Browser 中安装并启用 Server。'}
          countText={`已选择 ${mcpToolCount} 个 MCP 工具`}
          loading={mcpServersLoading}
          disabled={!hasEnabledMcpServers}
          tagColor="blue"
          selectedItems={selectedMcpTools}
          onOpen={onOpenMcpSelector}
        />

        <CapabilityPicker
          label="HTTP 工具"
          selectedText={`已选择 ${selectedKnowledgeTools.length} 个 HTTP 工具`}
          emptyText="未选择 HTTP 工具"
          help="可以在工具管理中维护 HTTP 工具，然后选择注入到当前 Agent。"
          countText={`已选择 ${knowledgeToolCount} 个 HTTP 工具`}
          loading={knowledgeToolsLoading}
          tagColor="green"
          selectedItems={selectedKnowledgeTools}
          onOpen={onOpenKnowledgeSelector}
        />

        <CapabilityPicker
          label="Skills"
          selectedText={`已选择 ${selectedSkills.length} 个 Skill`}
          emptyText="未选择 Skill"
          help={hasEnabledSkills
            ? '可以选择一个或多个已启用的 Skill。'
            : '请先在 Skills Browser 中安装并启用 Skill。'}
          countText={`已选择 ${skillCount} 个 Skill`}
          loading={skillsLoading}
          disabled={!hasEnabledSkills}
          tagColor="mauve"
          selectedItems={selectedSkills}
          onOpen={onOpenSkillsSelector}
        />
      </div>
    </section>
  );
}
