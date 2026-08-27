/**
 * AgentEditor — Agent 创建/编辑表单
 *
 * 使用 Compound Component 模式组织多个表单区块：
 * - Identity: 名称、头像、描述、系统提示词
 * - ModelConfig: 模型选择、Fallback 链、温度参数
 * - Tools: 默认工具（锁定）+ MCP + Skills
 * - Guardrails: 安全边界
 *
 * @see vercel-composition-patterns: architecture-compound-components
 */

import { useState, useEffect, useCallback, useMemo } from 'react';
import { ChevronDown, ChevronRight, Save, SlidersHorizontal, X } from 'lucide-react';
import {
  Alert,
  Button,
  Drawer,
} from '@/components/design';
import { AgentBasicSection } from './sections/AgentBasicSection';
import { AgentGuardrailsSection } from './sections/AgentGuardrailsSection';
import { AgentModelAdvancedSection } from './sections/AgentModelAdvancedSection';
import { AgentJobToolkitSection } from './sections/AgentJobToolkitSection';
import { AgentToolCapabilitiesSection } from './sections/AgentToolCapabilitiesSection';
import { KnowledgeToolSelectionModal } from './sections/KnowledgeToolSelectionModal';
import { McpToolSelectionModal } from './sections/McpToolSelectionModal';
import { SkillSelectionModal } from './sections/SkillSelectionModal';
import {
  EMPTY_HTTP_TOOL_DRAFT,
  draftToHttpToolPayload,
  httpToolToDraft,
  type HttpToolDraft,
} from '../tools/httpToolDraft';
import {
  HttpToolEditor,
} from '../tools/HttpToolEditor';
import type {
  Agent,
  AgentCreatePayload,
  ModelOption,
} from '../../types/agent';
import { DEFAULT_MODEL_CONFIG, createEmptyAgentForm } from '../../types/agent';
import { useMcp } from '../../hooks/useMcp';
import { useEnabledSkills } from '../../hooks/useSkills';
import { useHttpTools } from '../../hooks/useKnowledgeTools';
import type { HttpTool } from '../../types/tool';

function syncBoundIds(
  form: AgentCreatePayload,
  kind: 'mcp' | 'http' | 'skill',
  ids: string[],
): AgentCreatePayload {
  const unique = Array.from(new Set(ids));
  const keep = (item: { kind: string; id: string }) => item.kind !== kind || unique.includes(item.id);
  const existing = new Set([
    ...form.jobToolkit.required.filter((item) => item.kind === kind).map((item) => item.id),
    ...form.jobToolkit.optional.filter((item) => item.kind === kind).map((item) => item.id),
  ]);
  const added = unique.filter((id) => !existing.has(id)).map((id) => ({ kind, id }));
  const nextForm: AgentCreatePayload = {
    ...form,
    jobToolkit: {
      required: [...form.jobToolkit.required.filter(keep), ...added],
      optional: form.jobToolkit.optional.filter(keep),
    },
  };
  if (kind === 'mcp') nextForm.mcpTools = unique;
  if (kind === 'http') nextForm.knowledgeTools = unique;
  if (kind === 'skill') nextForm.skills = unique;
  return nextForm;
}

interface AgentEditorProps {
  /** 编辑的 Agent（null 表示创建模式） */
  agent: Agent | null;
  /** 可用模型列表 */
  models: {
    primary: ModelOption[];
    fallback: ModelOption[];
  } | null;
  /** 当前已有 Agent。保留字段兼容父组件调用。 */
  availableAgents?: Agent[];
  /** 保存回调 */
  onSave: (data: AgentCreatePayload) => Promise<void>;
  /** 取消回调 */
  onCancel: () => void;
}

/**
 * AgentEditor 组件
 */
export function AgentEditor({ agent, models, onSave, onCancel }: AgentEditorProps) {
  const [form, setForm] = useState<AgentCreatePayload>(createEmptyAgentForm());
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [mcpSelectorOpen, setMcpSelectorOpen] = useState(false);
  const [skillsSelectorOpen, setSkillsSelectorOpen] = useState(false);
  const [knowledgeSelectorOpen, setKnowledgeSelectorOpen] = useState(false);
  const [knowledgeEditorOpen, setKnowledgeEditorOpen] = useState(false);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [editingKnowledgeTool, setEditingKnowledgeTool] = useState<HttpTool | null>(null);
  const [knowledgeEditorError, setKnowledgeEditorError] = useState<string | null>(null);
  const [knowledgeDraft, setKnowledgeDraft] = useState<HttpToolDraft>(EMPTY_HTTP_TOOL_DRAFT);
  const { servers: mcpServers, loading: mcpServersLoading } = useMcp();
  const { skills: enabledSkills, loading: skillsLoading } = useEnabledSkills();
  const {
    tools: knowledgeTools,
    loading: knowledgeToolsLoading,
    createTool: createKnowledgeTool,
    updateTool: updateKnowledgeTool,
    deleteTool: deleteKnowledgeTool,
  } = useHttpTools();

  const enabledMcpServers = useMemo(() => (
    mcpServers.filter((server) => (
      server.status === 'installed' && server.enabled && server.tools.length > 0
    ))
  ), [mcpServers]);

  const selectedMcpTools = useMemo(() => (
    enabledMcpServers
      .flatMap((server) => server.tools)
      .filter((tool) => form.mcpTools.includes(tool.id))
  ), [enabledMcpServers, form.mcpTools]);

  const selectedKnowledgeTools = useMemo(() => (
    knowledgeTools.filter((tool) => form.knowledgeTools.includes(tool.id))
  ), [form.knowledgeTools, knowledgeTools]);

  const primaryModelOptions = useMemo(() => {
    return models?.primary ?? [];
  }, [models?.primary]);

  const fallbackModelOptions = useMemo(() => {
    return models?.fallback ?? [];
  }, [models?.fallback]);

  const selectedSkills = useMemo(() => (
    enabledSkills.filter((skill) => form.skills.includes(skill.id))
  ), [enabledSkills, form.skills]);

  // 编辑模式：填充表单
  useEffect(() => {
    if (agent) {
      setForm({
        name: agent.name,
        avatar: agent.avatar,
        description: agent.description,
        systemPrompt: agent.systemPrompt,
        modelConfig: { ...DEFAULT_MODEL_CONFIG, ...agent.modelConfig },
        mcpTools: [...agent.mcpTools],
        knowledgeTools: [...agent.knowledgeTools],
        skills: [...agent.skills],
        tags: [...agent.tags],
        planConfig: { ...agent.planConfig },
        guardrails: { ...agent.guardrails },
        role: agent.role || 'chat',
        jobToolkit: {
          required: [...(agent.jobToolkit?.required || [])],
          optional: [...(agent.jobToolkit?.optional || [])],
        },
      });
    } else {
      setForm(createEmptyAgentForm());
    }
    setError(null);
  }, [agent]);

  /** 保存表单 */
  const handleSave = useCallback(async () => {
    if (!form.name.trim()) {
      setError('请输入 Agent 名称');
      return;
    }
    if (form.role === 'job' && form.jobToolkit.required.length === 0) {
      setError('Job Agent 必须至少配置一项必注入工具');
      return;
    }

    setSaving(true);
    setError(null);
    try {
      await onSave(form);
    } catch (err) {
      setError(err instanceof Error ? err.message : '保存失败');
    } finally {
      setSaving(false);
    }
  }, [form, onSave]);

  const setMcpToolSelection = useCallback((toolIds: string[]) => {
    setForm((current) => syncBoundIds(current, 'mcp', toolIds));
  }, []);

  const toggleKnowledgeTool = useCallback((toolId: string, checked: boolean) => {
    setForm((current) => {
      const next = checked
        ? Array.from(new Set([...current.knowledgeTools, toolId]))
        : current.knowledgeTools.filter((id) => id !== toolId);
      return syncBoundIds(current, 'http', next);
    });
  }, []);

  const openKnowledgeEditor = useCallback((tool: HttpTool | null = null) => {
    setEditingKnowledgeTool(tool);
    setKnowledgeDraft(httpToolToDraft(tool));
    setKnowledgeEditorError(null);
    setKnowledgeEditorOpen(true);
  }, []);

  const handleSaveKnowledgeTool = useCallback(async () => {
    try {
      const payload = draftToHttpToolPayload(knowledgeDraft);
      const saved = editingKnowledgeTool
        ? await updateKnowledgeTool(editingKnowledgeTool.id, payload)
        : await createKnowledgeTool(payload);
      if (!editingKnowledgeTool) {
        toggleKnowledgeTool(saved.id, true);
      }
      setKnowledgeEditorOpen(false);
      setKnowledgeEditorError(null);
      setError(null);
    } catch (err) {
      setKnowledgeEditorError(err instanceof Error ? err.message : '保存 HTTP 工具失败，请检查 JSON 格式');
    }
  }, [createKnowledgeTool, editingKnowledgeTool, knowledgeDraft, toggleKnowledgeTool, updateKnowledgeTool]);

  const handleDeleteKnowledgeTool = useCallback(async (toolId: string) => {
    await deleteKnowledgeTool(toolId);
    setForm((current) => syncBoundIds(
      current,
      'http',
      current.knowledgeTools.filter((id) => id !== toolId),
    ));
  }, [deleteKnowledgeTool]);

  const toggleSkill = useCallback((skillId: string, checked: boolean) => {
    setForm((current) => {
      const next = checked
        ? Array.from(new Set([...current.skills, skillId]))
        : current.skills.filter((id) => id !== skillId);
      return syncBoundIds(current, 'skill', next);
    });
  }, []);

  return (
    <div className="h-full min-h-0 bg-[var(--color-bg-secondary)] p-4">
      <div className="grid h-full min-h-0 grid-rows-[auto_1fr] gap-4">
        <div className="mx-auto w-full max-w-5xl rounded-xl bg-[var(--color-bg-primary)] px-5 py-4 shadow-sm">
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <h2 className="text-lg font-medium text-[var(--color-text-primary)]">
                {agent ? '编辑 Agent' : '创建 Agent'}
              </h2>
              <div className="mt-1 text-xs text-[var(--color-text-tertiary)]">
                配置 Agent 的身份、模型、工具能力和运行边界。
              </div>
            </div>
            <div className="flex shrink-0 gap-2">
              <Button onClick={onCancel} icon={<X size={14} />}>
                取消
              </Button>
              <Button
                type="primary"
                onClick={handleSave}
                loading={saving}
                icon={<Save size={14} />}
              >
                {saving ? '保存中...' : '保存'}
              </Button>
            </div>
          </div>
        </div>

        <div className="min-h-0 overflow-y-auto">
          <div className="mx-auto w-full max-w-5xl space-y-4">
        {error && (
          <Alert type="error" message={error} showIcon />
        )}

        <AgentBasicSection
          form={form}
          primaryModelOptions={primaryModelOptions}
          onChange={setForm}
          lockRole={agent?.systemManaged === true}
        />

        <button
          type="button"
          onClick={() => setAdvancedOpen((value) => !value)}
          className="flex w-full items-center justify-between gap-3 rounded-xl bg-[var(--color-bg-primary)] px-4 py-3 text-left shadow-sm transition hover:bg-[var(--color-bg-hover)]"
        >
          <div className="flex min-w-0 items-center gap-3">
            <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[var(--color-bg-secondary)] text-[var(--color-text-tertiary)]">
              <SlidersHorizontal size={17} />
            </div>
            <div className="min-w-0">
              <div className="text-sm font-medium text-[var(--color-text-primary)]">高级设置</div>
              <div className="mt-0.5 text-xs text-[var(--color-text-tertiary)]">
                模型参数、工具、Skills、权限、计划和协作配置。
              </div>
            </div>
          </div>
          {advancedOpen ? (
            <ChevronDown size={16} className="shrink-0 text-[var(--color-text-tertiary)]" />
          ) : (
            <ChevronRight size={16} className="shrink-0 text-[var(--color-text-tertiary)]" />
          )}
        </button>

        <Drawer
          title="高级设置"
          open={advancedOpen}
          onClose={() => setAdvancedOpen(false)}
          width={760}
          showDivider={false}
          destroyOnClose={false}
          footer={(
            <div className="flex justify-end gap-2">
              <Button onClick={() => setAdvancedOpen(false)}>关闭</Button>
              <Button
                type="primary"
                onClick={handleSave}
                loading={saving}
                icon={<Save size={14} />}
              >
                {saving ? '保存中...' : '保存'}
              </Button>
            </div>
          )}
        >
          <div className="space-y-4 pb-4">

        {form.role === 'job' ? (
          <AgentJobToolkitSection
            form={form}
            selectedMcp={selectedMcpTools.map((tool) => ({ id: tool.id, name: tool.name }))}
            selectedHttp={selectedKnowledgeTools.map((tool) => ({ id: tool.id, name: tool.name }))}
            selectedSkills={selectedSkills.map((skill) => ({ id: skill.id, name: skill.name }))}
            onChange={setForm}
            onOpenMcp={() => setMcpSelectorOpen(true)}
            onOpenHttp={() => setKnowledgeSelectorOpen(true)}
            onOpenSkills={() => setSkillsSelectorOpen(true)}
          />
        ) : (
          <AgentToolCapabilitiesSection
            selectedMcpTools={selectedMcpTools}
            selectedKnowledgeTools={selectedKnowledgeTools}
            selectedSkills={selectedSkills}
            mcpToolCount={form.mcpTools.length}
            knowledgeToolCount={form.knowledgeTools.length}
            skillCount={form.skills.length}
            mcpServersLoading={mcpServersLoading}
            knowledgeToolsLoading={knowledgeToolsLoading}
            skillsLoading={skillsLoading}
            hasEnabledMcpServers={enabledMcpServers.length > 0}
            hasEnabledSkills={enabledSkills.length > 0}
            onOpenMcpSelector={() => setMcpSelectorOpen(true)}
            onOpenKnowledgeSelector={() => setKnowledgeSelectorOpen(true)}
            onOpenSkillsSelector={() => setSkillsSelectorOpen(true)}
          />
        )}

        <AgentModelAdvancedSection
          form={form}
          fallbackModelOptions={fallbackModelOptions}
          onChange={setForm}
        />

        <AgentGuardrailsSection
          form={form}
          onChange={setForm}
          lockUnattendedPolicy={agent?.systemManaged === true && agent.role === 'job'}
        />

          </div>
        </Drawer>
          </div>
        </div>
      </div>

      <McpToolSelectionModal
        open={mcpSelectorOpen}
        servers={enabledMcpServers}
        selectedToolIds={form.mcpTools}
        onSelectedToolIdsChange={setMcpToolSelection}
        onClose={() => setMcpSelectorOpen(false)}
      />

      <KnowledgeToolSelectionModal
        open={knowledgeSelectorOpen}
        tools={knowledgeTools}
        selectedToolIds={form.knowledgeTools}
        onToggleTool={toggleKnowledgeTool}
        onEditTool={openKnowledgeEditor}
        onDeleteTool={(toolId) => void handleDeleteKnowledgeTool(toolId)}
        onClose={() => setKnowledgeSelectorOpen(false)}
      />

      <HttpToolEditor
        open={knowledgeEditorOpen}
        editingTool={editingKnowledgeTool}
        draft={knowledgeDraft}
        saving={false}
        error={knowledgeEditorError}
        onDraftChange={setKnowledgeDraft}
        onCancel={() => setKnowledgeEditorOpen(false)}
        onSave={() => void handleSaveKnowledgeTool()}
      />

      <SkillSelectionModal
        open={skillsSelectorOpen}
        skills={enabledSkills}
        selectedSkillIds={form.skills}
        onToggleSkill={toggleSkill}
        onClose={() => setSkillsSelectorOpen(false)}
      />
    </div>
  );
}
