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
import { ChevronDown, ChevronRight, Pencil, Plus, Save, SlidersHorizontal, Trash2, X } from 'lucide-react';
import {
  Alert,
  Button,
  Checkbox,
  Drawer,
  Input,
  InputNumber,
  Modal,
  Select,
  Slider,
  Tag,
} from '@agentscope-ai/design';
import { ToggleButton } from '../../components/ToggleButton';
import type {
  Agent,
  AgentCreatePayload,
  ModelOption,
} from '../../types/agent';
import { DEFAULT_MODEL_CONFIG, createEmptyAgentForm } from '../../types/agent';
import { useMcp } from '../../hooks/useMcp';
import { useEnabledSkills } from '../../hooks/useSkills';
import { useHttpTools } from '../../hooks/useKnowledgeTools';
import type { HttpTool, HttpToolPayload } from '../../types/tool';

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

function SquareAddButton({
  ariaLabel,
  disabled = false,
  loading = false,
  onClick,
}: {
  ariaLabel: string;
  disabled?: boolean;
  loading?: boolean;
  onClick: () => void;
}) {
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

const DEFAULT_HTTP_PARAMETER_SCHEMA = {
  type: 'object',
  properties: {
    query: { type: 'string', description: '检索问题或 API 查询参数。' },
    filename_pattern: { type: 'string', description: '可选文件名过滤。' },
    filterCategories: { type: 'array', items: { type: 'string' }, description: '可选分类过滤。' },
    searchMode: { type: 'string', description: '可选检索模式。' },
  },
};

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
  const [knowledgeDraft, setKnowledgeDraft] = useState({
    name: 'KnowledgeBaseSearch',
    description: '检索外部知识库并返回命中文档片段。',
    method: 'POST' as HttpTool['method'],
    url: 'http://localhost:8080/search/knowledgeBaseSearch',
    headersJson: '{}',
    queryTemplateJson: '{}',
    defaultPayloadJson: JSON.stringify({
      query: '',
      filename_pattern: '',
      filterCategories: [],
      searchMode: '',
    }, null, 2),
    parameterSchemaJson: JSON.stringify(DEFAULT_HTTP_PARAMETER_SCHEMA, null, 2),
    parameterInstructions: 'query 是检索问题；filename_pattern 用于按文件名过滤；filterCategories 是分类过滤数组；searchMode 是检索模式。',
    enabled: true,
  });
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

  const selectedMcpToolSet = useMemo(() => (
    new Set(form.mcpTools)
  ), [form.mcpTools]);

  const selectedMcpTools = useMemo(() => (
    enabledMcpServers
      .flatMap((server) => server.tools)
      .filter((tool) => selectedMcpToolSet.has(tool.id))
  ), [enabledMcpServers, selectedMcpToolSet]);

  const selectedKnowledgeToolSet = useMemo(() => (
    new Set(form.knowledgeTools)
  ), [form.knowledgeTools]);

  const selectedKnowledgeTools = useMemo(() => (
    knowledgeTools.filter((tool) => selectedKnowledgeToolSet.has(tool.id))
  ), [knowledgeTools, selectedKnowledgeToolSet]);

  const primaryModelOptions = useMemo(() => {
    return models?.primary ?? [];
  }, [models?.primary]);

  const fallbackModelOptions = useMemo(() => {
    return models?.fallback ?? [];
  }, [models?.fallback]);

  const selectedSkillSet = useMemo(() => (
    new Set(form.skills)
  ), [form.skills]);

  const selectedSkills = useMemo(() => (
    enabledSkills.filter((skill) => selectedSkillSet.has(skill.id))
  ), [enabledSkills, selectedSkillSet]);

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
        callableAgents: [...agent.callableAgents],
        maxDepth: agent.maxDepth,
        memoryEnabled: agent.memoryEnabled,
        planConfig: { ...agent.planConfig },
        guardrails: { ...agent.guardrails },
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
    setForm((current) => ({
      ...current,
      mcpTools: Array.from(new Set(toolIds)),
    }));
  }, []);

  const toggleMcpServer = useCallback((serverId: string, checked: boolean) => {
    const server = enabledMcpServers.find((item) => item.id === serverId);
    if (!server) return;

    const serverToolIds = server.tools.map((tool) => tool.id);
    if (checked) {
      setMcpToolSelection([...form.mcpTools, ...serverToolIds]);
      return;
    }

    setMcpToolSelection(form.mcpTools.filter((toolId) => !serverToolIds.includes(toolId)));
  }, [enabledMcpServers, form.mcpTools, setMcpToolSelection]);

  const toggleMcpTool = useCallback((toolId: string, checked: boolean) => {
    if (checked) {
      setMcpToolSelection([...form.mcpTools, toolId]);
      return;
    }

    setMcpToolSelection(form.mcpTools.filter((id) => id !== toolId));
  }, [form.mcpTools, setMcpToolSelection]);

  const toggleKnowledgeTool = useCallback((toolId: string, checked: boolean) => {
    if (checked) {
      setForm((current) => ({
        ...current,
        knowledgeTools: Array.from(new Set([...current.knowledgeTools, toolId])),
      }));
      return;
    }

    setForm((current) => ({
      ...current,
      knowledgeTools: current.knowledgeTools.filter((id) => id !== toolId),
    }));
  }, []);

  const openKnowledgeEditor = useCallback((tool: HttpTool | null = null) => {
    setEditingKnowledgeTool(tool);
    setKnowledgeDraft({
      name: tool?.name ?? 'KnowledgeBaseSearch',
      description: tool?.description ?? '检索外部知识库并返回命中文档片段。',
      method: tool?.method ?? 'POST',
      url: tool?.url ?? 'http://localhost:8080/search/knowledgeBaseSearch',
      headersJson: JSON.stringify(tool?.headers ?? {}, null, 2),
      queryTemplateJson: JSON.stringify(tool?.queryTemplate ?? {}, null, 2),
      defaultPayloadJson: JSON.stringify(tool?.bodyTemplate ?? {
        query: '',
        filename_pattern: '',
        filterCategories: [],
        searchMode: '',
      }, null, 2),
      parameterSchemaJson: JSON.stringify(tool?.parameterSchema ?? DEFAULT_HTTP_PARAMETER_SCHEMA, null, 2),
      parameterInstructions: tool?.parameterInstructions ?? 'query 是检索问题；filename_pattern 用于按文件名过滤；filterCategories 是分类过滤数组；searchMode 是检索模式。',
      enabled: tool?.enabled ?? true,
    });
    setKnowledgeEditorOpen(true);
  }, []);

  const handleSaveKnowledgeTool = useCallback(async () => {
    try {
      const headers = JSON.parse(knowledgeDraft.headersJson || '{}');
      const queryTemplate = JSON.parse(knowledgeDraft.queryTemplateJson || '{}');
      const bodyTemplate = JSON.parse(knowledgeDraft.defaultPayloadJson || '{}');
      const parameterSchema = JSON.parse(knowledgeDraft.parameterSchemaJson || '{}');
      const sendsBody = !['GET', 'DELETE'].includes(knowledgeDraft.method);
      const payload: HttpToolPayload = {
        name: knowledgeDraft.name,
        description: knowledgeDraft.description,
        category: 'custom',
        method: knowledgeDraft.method,
        url: knowledgeDraft.url,
        authType: 'none',
        authConfig: {},
        headers,
        queryTemplate,
        bodyTemplate: sendsBody ? bodyTemplate : {},
        parameterSchema,
        parameterInstructions: knowledgeDraft.parameterInstructions,
        responseMode: 'full',
        responsePath: '',
        readonly: true,
        approvalPolicy: 'inherit',
        enabled: knowledgeDraft.enabled,
      };
      const saved = editingKnowledgeTool
        ? await updateKnowledgeTool(editingKnowledgeTool.id, payload)
        : await createKnowledgeTool(payload);
      if (!editingKnowledgeTool) {
        toggleKnowledgeTool(saved.id, true);
      }
      setKnowledgeEditorOpen(false);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : '保存 HTTP 工具失败，请检查 JSON 格式');
    }
  }, [createKnowledgeTool, editingKnowledgeTool, knowledgeDraft, toggleKnowledgeTool, updateKnowledgeTool]);

  const handleDeleteKnowledgeTool = useCallback(async (toolId: string) => {
    await deleteKnowledgeTool(toolId);
    setForm((current) => ({
      ...current,
      knowledgeTools: current.knowledgeTools.filter((id) => id !== toolId),
    }));
  }, [deleteKnowledgeTool]);

  const toggleSkill = useCallback((skillId: string, checked: boolean) => {
    if (checked) {
      setForm((current) => ({
        ...current,
        skills: Array.from(new Set([...current.skills, skillId])),
      }));
      return;
    }

    setForm((current) => ({
      ...current,
      skills: current.skills.filter((id) => id !== skillId),
    }));
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

        <section className="rounded-xl bg-[var(--color-bg-primary)] p-4 shadow-sm">
          <div className="mb-4 flex items-center justify-between gap-3">
            <div>
              <h3 className="text-sm font-medium text-[var(--color-text-primary)]">
                基础配置
              </h3>
              <div className="mt-1 text-xs text-[var(--color-text-tertiary)]">
                只保留最常用的 Agent 信息；工具、权限、计划和模型参数放在高级设置中。
              </div>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="mb-1 block text-xs text-[var(--color-text-secondary)]">
                名称 <span className="text-[var(--color-error)]">*</span>
              </label>
              <Input
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="如：CodeReviewer"
                maxLength={50}
              />
            </div>
            <div>
              <label className="mb-1 block text-xs text-[var(--color-text-secondary)]">
                描述
              </label>
              <Input
                value={form.description}
                onChange={(e) => setForm({ ...form, description: e.target.value })}
                placeholder="一句话描述这个 Agent 的用途"
                maxLength={200}
              />
            </div>
            <div className="col-span-2">
              {primaryModelOptions.length === 0 && (
                <div className="mb-3">
                  <Alert
                    type="warning"
                    message="当前系统设置中的供应商未配置可用模型。请先到系统设置为当前供应商配置 API Key 和模型。"
                  />
                </div>
              )}
              <label className="block text-xs text-[var(--color-text-secondary)] mb-1">
                使用模型
              </label>
              <Select
                value={form.modelConfig.primary}
                onChange={(value) =>
                  setForm({
                    ...form,
                    modelConfig: { ...form.modelConfig, primary: value },
                  })
                }
                options={primaryModelOptions.map((m) => ({
                  label: `${m.name} - ${m.description}`,
                  value: m.id,
                }))}
                style={{ width: '100%' }}
              />
            </div>
            <div className="col-span-2">
              <label className="mb-1 block text-xs text-[var(--color-text-secondary)]">
                系统提示词
              </label>
              <Input.TextArea
                value={form.systemPrompt}
                onChange={(e) => setForm({ ...form, systemPrompt: e.target.value })}
                placeholder="定义 Agent 的角色和行为..."
                rows={10}
                style={{ fontFamily: 'var(--font-mono)' }}
              />
            </div>
          </div>
        </section>

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
            {/* 默认工具（锁定） */}
            <div>
              <label className="block text-xs text-[var(--color-text-secondary)] mb-1">
                默认工具（已锁定）
              </label>
              <div className="flex flex-wrap gap-2">
                {['Read', 'Write', 'Edit', 'Grep', 'Glob', 'Bash', 'HttpRequest'].map((tool) => (
                  <Tag
                    key={tool}
                    color="transparent"
                    size="small"
                  >
                    {tool}
                  </Tag>
                ))}
              </div>
            </div>

            {/* MCP 工具 */}
            <div>
              <label className="block text-xs text-[var(--color-text-secondary)] mb-1">
                MCP 工具
              </label>
              <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-primary)] p-3">
                <div className="flex items-center justify-between gap-3">
                  <div className="min-w-0 text-sm text-[var(--color-text-secondary)]">
                    {selectedMcpTools.length > 0
                      ? `已选择 ${selectedMcpTools.length} 个 MCP 工具`
                      : '未选择 MCP 工具'}
                  </div>
                  <SquareAddButton
                    ariaLabel="选择 MCP 工具"
                    loading={mcpServersLoading}
                    disabled={enabledMcpServers.length === 0}
                    onClick={() => setMcpSelectorOpen(true)}
                  />
                </div>
                {selectedMcpTools.length > 0 ? (
                  <div className="mt-3 flex max-h-[72px] flex-wrap gap-2 overflow-hidden">
                    {selectedMcpTools.slice(0, 8).map((tool) => (
                      <Tag key={tool.id} size="small" color="blue">
                        {tool.name}
                      </Tag>
                    ))}
                    {selectedMcpTools.length > 8 && (
                      <Tag size="small" color="transparent">
                        +{selectedMcpTools.length - 8}
                      </Tag>
                    )}
                  </div>
                ) : (
                  <div className="mt-2 text-xs text-[var(--color-text-tertiary)]">
                    {enabledMcpServers.length > 0
                      ? '可以选择整个 MCP Server，或只选择其中某个工具。'
                      : '请先在 MCP Browser 中安装并启用 Server。'}
                  </div>
                )}
              </div>
              <div className="mt-1 text-xs text-[var(--color-text-tertiary)]">
                已选择 {form.mcpTools.length} 个 MCP 工具
              </div>
            </div>

            {/* HTTP 工具 */}
            <div>
              <label className="block text-xs text-[var(--color-text-secondary)] mb-1">
                HTTP 工具
              </label>
              <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-primary)] p-3">
                <div className="flex items-center justify-between gap-3">
                  <div className="min-w-0 text-sm text-[var(--color-text-secondary)]">
                    {selectedKnowledgeTools.length > 0
                      ? `已选择 ${selectedKnowledgeTools.length} 个 HTTP 工具`
                      : '未选择 HTTP 工具'}
                  </div>
                  <div className="flex shrink-0 gap-2">
                    <SquareAddButton
                      ariaLabel="选择 HTTP 工具"
                      loading={knowledgeToolsLoading}
                      onClick={() => setKnowledgeSelectorOpen(true)}
                    />
                  </div>
                </div>
                {selectedKnowledgeTools.length > 0 ? (
                  <div className="mt-3 flex max-h-[72px] flex-wrap gap-2 overflow-hidden">
                    {selectedKnowledgeTools.slice(0, 8).map((tool) => (
                      <Tag key={tool.id} size="small" color="green">
                        {tool.name}
                      </Tag>
                    ))}
                    {selectedKnowledgeTools.length > 8 && (
                      <Tag size="small" color="transparent">
                        +{selectedKnowledgeTools.length - 8}
                      </Tag>
                    )}
                  </div>
                ) : (
                  <div className="mt-2 text-xs text-[var(--color-text-tertiary)]">
                    可以在工具管理中维护 HTTP 工具，然后选择注入到当前 Agent。
                  </div>
                )}
              </div>
              <div className="mt-1 text-xs text-[var(--color-text-tertiary)]">
                已选择 {form.knowledgeTools.length} 个 HTTP 工具
              </div>
            </div>

            {/* Skills */}
            <div>
              <label className="block text-xs text-[var(--color-text-secondary)] mb-1">
                Skills
              </label>
              <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-primary)] p-3">
                <div className="flex items-center justify-between gap-3">
                  <div className="min-w-0 text-sm text-[var(--color-text-secondary)]">
                    {selectedSkills.length > 0
                      ? `已选择 ${selectedSkills.length} 个 Skill`
                      : '未选择 Skill'}
                  </div>
                  <SquareAddButton
                    ariaLabel="选择 Skills"
                    loading={skillsLoading}
                    disabled={enabledSkills.length === 0}
                    onClick={() => setSkillsSelectorOpen(true)}
                  />
                </div>
                {selectedSkills.length > 0 ? (
                  <div className="mt-3 flex max-h-[72px] flex-wrap gap-2 overflow-hidden">
                    {selectedSkills.slice(0, 8).map((skill) => (
                      <Tag key={skill.id} size="small" color="mauve">
                        {skill.name}
                      </Tag>
                    ))}
                    {selectedSkills.length > 8 && (
                      <Tag size="small" color="transparent">
                        +{selectedSkills.length - 8}
                      </Tag>
                    )}
                  </div>
                ) : (
                  <div className="mt-2 text-xs text-[var(--color-text-tertiary)]">
                    {enabledSkills.length > 0
                      ? '可以选择一个或多个已启用的 Skill。'
                      : '请先在 Skills Browser 中安装并启用 Skill。'}
                  </div>
                )}
              </div>
              <div className="mt-1 text-xs text-[var(--color-text-tertiary)]">
                已选择 {form.skills.length} 个 Skill
              </div>
            </div>
          </div>
        </section>

        <section className="rounded-xl bg-[var(--color-bg-primary)] p-4 shadow-sm">
          <div className="mb-4">
            <h3 className="text-sm font-medium text-[var(--color-text-primary)]">
              模型高级参数
            </h3>
            <div className="mt-1 text-xs text-[var(--color-text-tertiary)]">
              配置降级模型链、生成随机性、输出长度和 ReAct 循环上限。
            </div>
          </div>
          <div className="space-y-3">
            <div>
              <label className="block text-xs text-[var(--color-text-secondary)] mb-1">
                降级模型链
              </label>
              <div className="space-y-2">
                {form.modelConfig.fallback.map((fb, idx) => (
                  <div key={idx} className="flex gap-2">
                    <Select
                      value={fb}
                      onChange={(value) => {
                        const newFallback = [...form.modelConfig.fallback];
                        newFallback[idx] = value;
                        setForm({
                          ...form,
                          modelConfig: { ...form.modelConfig, fallback: newFallback },
                        });
                      }}
                      options={fallbackModelOptions.map((m) => ({
                        label: m.name,
                        value: m.id,
                      }))}
                      style={{ flex: 1 }}
                    />
                    {form.modelConfig.fallback.length > 1 && (
                      <Button
                        danger
                        type="text"
                        onClick={() => {
                          const newFallback = form.modelConfig.fallback.filter((_, i) => i !== idx);
                          setForm({
                            ...form,
                            modelConfig: { ...form.modelConfig, fallback: newFallback },
                          });
                        }}
                      >
                        ×
                      </Button>
                    )}
                  </div>
                ))}
                <Button
                  type="link"
                  size="small"
                  onClick={() =>
                    setForm({
                      ...form,
                      modelConfig: {
                        ...form.modelConfig,
                        fallback: [...form.modelConfig.fallback, 'deepseek-reasoner'],
                      },
                    })
                  }
                >
                  + 添加降级模型
                </Button>
              </div>
            </div>

            <div>
              <label className="block text-xs text-[var(--color-text-secondary)] mb-1">
                温度: {form.modelConfig.temperature.toFixed(1)}
              </label>
              <Slider
                min={0}
                max={2}
                step={0.1}
                value={form.modelConfig.temperature}
                onChange={(value) =>
                  setForm({
                    ...form,
                    modelConfig: { ...form.modelConfig, temperature: value },
                  })
                }
              />
              <div className="flex justify-between text-xs text-[var(--color-text-tertiary)]">
                <span>精确 (0)</span>
                <span>创意 (2)</span>
              </div>
            </div>

            <div>
              <label className="block text-xs text-[var(--color-text-secondary)] mb-1">
                Top P: {form.modelConfig.topP.toFixed(2)}
              </label>
              <Slider
                min={0}
                max={1}
                step={0.05}
                value={form.modelConfig.topP}
                onChange={(value) =>
                  setForm({
                    ...form,
                    modelConfig: { ...form.modelConfig, topP: value },
                  })
                }
              />
              <div className="flex justify-between text-xs text-[var(--color-text-tertiary)]">
                <span>保守 (0)</span>
                <span>多样 (1)</span>
              </div>
            </div>

            <div>
              <label className="block text-xs text-[var(--color-text-secondary)] mb-1">
                最大输出 Token
              </label>
              <InputNumber
                min={256}
                max={128000}
                value={form.modelConfig.maxTokens}
                onChange={(value) =>
                  setForm({
                    ...form,
                    modelConfig: { ...form.modelConfig, maxTokens: Number(value ?? 4096) },
                  })
                }
                style={{ width: 160 }}
              />
            </div>

            <div>
              <label className="block text-xs text-[var(--color-text-secondary)] mb-1">
                ReAct 最大循环次数
              </label>
              <InputNumber
                min={1}
                max={120}
                value={form.modelConfig.maxReactIters}
                onChange={(value) =>
                  setForm({
                    ...form,
                    modelConfig: { ...form.modelConfig, maxReactIters: Number(value ?? 40) },
                  })
                }
                style={{ width: 160 }}
              />
              <div className="mt-1 text-xs text-[var(--color-text-tertiary)]">
                复杂 Skills 或多工具任务建议 40-80；过高会增加耗时和费用。
              </div>
            </div>
          </div>
        </section>

        <section className="rounded-xl bg-[var(--color-bg-primary)] p-4 shadow-sm">
          <div className="mb-4">
            <h3 className="text-sm font-medium text-[var(--color-text-primary)]">
              安全边界
            </h3>
            <div className="mt-1 text-xs text-[var(--color-text-tertiary)]">
              控制工具执行的网络、审批和超时策略。
            </div>
          </div>
          <div className="space-y-3">
            {/* 网络访问 */}
            <div className="flex items-center justify-between gap-4 rounded-md border border-[var(--color-border)] bg-[var(--color-bg-primary)] px-3 py-2">
              <div className="text-sm text-[var(--color-text-primary)]">允许网络访问</div>
              <ToggleButton
                checked={form.guardrails.networkEnabled}
                onChange={(checked) =>
                  setForm({
                    ...form,
                    guardrails: { ...form.guardrails, networkEnabled: checked },
                  })
                }
                checkedLabel="开启"
                uncheckedLabel="关闭"
                ariaLabel="允许网络访问"
              />
            </div>

            {/* 执行隔离 */}
            <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-primary)] px-3 py-3">
              <div className="mb-2">
                <div className="text-sm text-[var(--color-text-primary)]">执行隔离</div>
                <div className="mt-1 text-xs text-[var(--color-text-tertiary)]">
                  Docker 会把工具运行放入容器工作区，需要本机 Docker 可用。
                </div>
              </div>
              <Select
                value={form.guardrails.executionBackend}
                onChange={(value) =>
                  setForm({
                    ...form,
                    guardrails: {
                      ...form.guardrails,
                      executionBackend: value as 'local' | 'docker',
                    },
                  })
                }
                options={[
                  { label: '本地工作区', value: 'local' },
                  { label: 'Docker 沙盒', value: 'docker' },
                ]}
                style={{ width: '100%' }}
              />
              {form.guardrails.executionBackend === 'docker' && (
                <div className="mt-3 grid grid-cols-[1fr_auto] gap-3">
                  <div>
                    <label className="mb-1 block text-xs text-[var(--color-text-secondary)]">
                      Docker 基础镜像
                    </label>
                    <Input
                      value={form.guardrails.dockerImage}
                      onChange={(event) =>
                        setForm({
                          ...form,
                          guardrails: { ...form.guardrails, dockerImage: event.target.value },
                        })
                      }
                      placeholder="python:3.11-slim"
                    />
                  </div>
                  <div className="flex items-end pb-1">
                    <ToggleButton
                      checked={form.guardrails.persistentSandbox}
                      onChange={(checked) =>
                        setForm({
                          ...form,
                          guardrails: { ...form.guardrails, persistentSandbox: checked },
                        })
                      }
                      checkedLabel="持久"
                      uncheckedLabel="临时"
                      ariaLabel="Docker 沙盒持久化"
                    />
                  </div>
                </div>
              )}
            </div>

            {/* 人工审批 */}
            <div className="flex items-center justify-between gap-4 rounded-md border border-[var(--color-border)] bg-[var(--color-bg-primary)] px-3 py-2">
              <div>
                <div className="text-sm text-[var(--color-text-primary)]">强制人工审批</div>
                <div className="mt-1 text-xs text-[var(--color-text-tertiary)]">
                  开启后会覆盖下方权限范围，工具调用按每次确认处理。
                </div>
              </div>
              <ToggleButton
                checked={form.guardrails.humanApproval}
                onChange={(checked) =>
                  setForm({
                    ...form,
                    guardrails: { ...form.guardrails, humanApproval: checked },
                  })
                }
                checkedLabel="开启"
                uncheckedLabel="关闭"
                ariaLabel="启用人工审批"
              />
            </div>

            {/* 工具权限范围 */}
            <div>
              <label className="block text-xs text-[var(--color-text-secondary)] mb-1">
                工具权限范围
              </label>
              <Select
                value={form.guardrails.toolPermissionProfile}
                disabled={form.guardrails.humanApproval}
                onChange={(value) =>
                  setForm({
                    ...form,
                    guardrails: {
                      ...form.guardrails,
                      toolPermissionProfile: value as typeof form.guardrails.toolPermissionProfile,
                      toolPermissionMode: legacyModeFromProfile(String(value)),
                    },
                  })
                }
                options={[
                  { label: '只读工具 - 仅读取/搜索文件', value: 'read_only' },
                  { label: '可信工具 - 自动放行', value: 'trusted_tools' },
                  { label: '破坏性操作确认 - 推荐', value: 'confirm_destructive' },
                  { label: '每次工具调用都确认', value: 'ask_every_time' },
                ]}
                style={{ width: '100%' }}
              />
              <div className="mt-1 text-xs text-[var(--color-text-tertiary)]">
                只读模式会移除写入、Bash、HTTP 和 MCP；可信工具适合受控沙箱；破坏性确认适合日常开发。
              </div>
            </div>

            {/* 超时 */}
            <div>
              <label className="block text-xs text-[var(--color-text-secondary)] mb-1">
                工具调用超时（秒）
              </label>
              <InputNumber
                min={10}
                max={3600}
                value={form.guardrails.timeoutSeconds}
                onChange={(value) =>
                  setForm({
                    ...form,
                    guardrails: { ...form.guardrails, timeoutSeconds: Number(value ?? 10) },
                  })
                }
                style={{ width: 128 }}
              />
            </div>
          </div>
        </section>

          </div>
        </Drawer>
          </div>
        </div>
      </div>

      <Modal
        title="选择 MCP 工具"
        open={mcpSelectorOpen}
        onCancel={() => setMcpSelectorOpen(false)}
        onOk={() => setMcpSelectorOpen(false)}
        okText="完成"
        cancelText="关闭"
        width={720}
      >
        {enabledMcpServers.length === 0 ? (
          <div className="py-8 text-center text-sm text-[var(--color-text-tertiary)]">
            暂无已安装并启用的 MCP Server。
          </div>
        ) : (
          <div className="max-h-[520px] space-y-3 overflow-y-auto pr-1">
            {enabledMcpServers.map((server) => {
              const serverToolIds = server.tools.map((tool) => tool.id);
              const selectedCount = serverToolIds.filter((toolId) => selectedMcpToolSet.has(toolId)).length;
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
                      onChange={(event) => toggleMcpServer(server.id, event.target.checked)}
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
                            checked={selectedMcpToolSet.has(tool.id)}
                            onChange={(event) => toggleMcpTool(tool.id, event.target.checked)}
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

      <Modal
        title="选择 HTTP 工具"
        open={knowledgeSelectorOpen}
        onCancel={() => setKnowledgeSelectorOpen(false)}
        onOk={() => setKnowledgeSelectorOpen(false)}
        okText="完成"
        cancelText="关闭"
        width={760}
      >
        {knowledgeTools.length === 0 ? (
          <div className="py-8 text-center text-sm text-[var(--color-text-tertiary)]">
            暂无 HTTP 工具。请先到工具管理中配置可注入 Agent 的 HTTP 工具。
          </div>
        ) : (
          <div className="max-h-[520px] space-y-2 overflow-y-auto pr-1">
            {knowledgeTools.map((tool) => (
              <div
                key={tool.id}
                className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-primary)] px-4 py-3"
              >
                <div className="flex items-start justify-between gap-3">
                  <Checkbox
                    checked={selectedKnowledgeToolSet.has(tool.id)}
                    disabled={!tool.enabled}
                    onChange={(event) => toggleKnowledgeTool(tool.id, event.target.checked)}
                  >
                    <span className="font-medium text-[var(--color-text-primary)]">
                      {tool.name}
                    </span>
                  </Checkbox>
                  <div className="flex shrink-0 gap-1">
                    <Button
                      type="text"
                      icon={<Pencil size={14} />}
                      onClick={() => openKnowledgeEditor(tool)}
                      aria-label="编辑 HTTP 工具"
                    />
                    <Button
                      danger
                      type="text"
                      icon={<Trash2 size={14} />}
                      onClick={() => handleDeleteKnowledgeTool(tool.id)}
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

      <Modal
        title={editingKnowledgeTool ? '编辑 HTTP 工具' : '新增 HTTP 工具'}
        open={knowledgeEditorOpen}
        onCancel={() => setKnowledgeEditorOpen(false)}
        onOk={handleSaveKnowledgeTool}
        okText="保存"
        cancelText="取消"
        width={840}
      >
        {(() => {
          const sendsBody = !['GET', 'DELETE'].includes(knowledgeDraft.method);
          const methodDescription = sendsBody
            ? '该请求会把 Body 模板作为 JSON 请求体发送；Query 模板会拼接到 URL 查询参数。'
            : '该请求不会发送 Body，参数会合并到 Query 模板并拼接到 URL 查询参数。';
          return (
        <div className="http-tool-form">
          <div className="http-tool-form__section">
            <div className="http-tool-form__section-title">基础信息</div>
            <div className="http-tool-form__grid">
          <div>
            <label className="mb-1 block text-xs text-[var(--color-text-secondary)]">
              工具名称
            </label>
            <Input
              value={knowledgeDraft.name}
              onChange={(event) => setKnowledgeDraft({ ...knowledgeDraft, name: event.target.value })}
              placeholder="如：公司知识库检索"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs text-[var(--color-text-secondary)]">
              是否启用
            </label>
            <ToggleButton
              checked={knowledgeDraft.enabled}
              checkedLabel="启用"
              uncheckedLabel="停用"
              ariaLabel="启用 HTTP 工具"
              onChange={(checked) => setKnowledgeDraft({ ...knowledgeDraft, enabled: checked })}
            />
          </div>
            </div>
          </div>
          <div className="http-tool-form__section">
            <div className="http-tool-form__section-title">请求配置</div>
            <div className="http-tool-form__grid">
          <div>
            <label className="mb-1 block text-xs text-[var(--color-text-secondary)]">
              请求类型
            </label>
            <Select
              value={knowledgeDraft.method}
              onChange={(value) => setKnowledgeDraft({ ...knowledgeDraft, method: value as HttpTool['method'] })}
              options={[
                { label: 'GET - 查询读取', value: 'GET' },
                { label: 'POST - 提交/检索', value: 'POST' },
                { label: 'PUT - 整体更新', value: 'PUT' },
                { label: 'PATCH - 局部更新', value: 'PATCH' },
                { label: 'DELETE - 删除/撤销', value: 'DELETE' },
              ]}
            />
          </div>
          <div>
            <label className="mb-1 block text-xs text-[var(--color-text-secondary)]">
              发送方式
            </label>
            <div className="http-tool-form__hint">
              {methodDescription}
            </div>
          </div>
            </div>
          <div className="col-span-2">
            <label className="mb-1 block text-xs text-[var(--color-text-secondary)]">
              接口地址
            </label>
            <Input
              value={knowledgeDraft.url}
              onChange={(event) => setKnowledgeDraft({ ...knowledgeDraft, url: event.target.value })}
              placeholder="http://localhost:8080/search/knowledgeBaseSearch"
            />
          </div>
          </div>
          <div className="http-tool-form__section">
            <div className="http-tool-form__section-title">请求头</div>
          <div>
            <label className="mb-1 block text-xs text-[var(--color-text-secondary)]">
              Headers JSON
            </label>
            <Input.TextArea
              className="http-tool-form__json"
              rows={7}
              value={knowledgeDraft.headersJson}
              onChange={(event) => setKnowledgeDraft({ ...knowledgeDraft, headersJson: event.target.value })}
              style={{ fontFamily: 'var(--font-mono)' }}
            />
          </div>
          </div>
          <div className="http-tool-form__section">
            <div className="http-tool-form__section-title">参数配置</div>
          <div>
            <label className="mb-1 block text-xs text-[var(--color-text-secondary)]">
              工具描述
            </label>
            <Input.TextArea
              className="http-tool-form__text"
              rows={3}
              value={knowledgeDraft.description}
              onChange={(event) => setKnowledgeDraft({ ...knowledgeDraft, description: event.target.value })}
              placeholder="告诉模型什么时候应该调用这个 HTTP 工具"
            />
          </div>
          <div className="mt-3">
            <label className="mb-1 block text-xs text-[var(--color-text-secondary)]">
              参数说明
            </label>
            <Input.TextArea
              className="http-tool-form__text"
              rows={3}
              value={knowledgeDraft.parameterInstructions}
              onChange={(event) => setKnowledgeDraft({ ...knowledgeDraft, parameterInstructions: event.target.value })}
              placeholder="说明 query、filename_pattern、filterCategories、searchMode 应该如何填写"
            />
          </div>
          <div className="http-tool-form__grid mt-3">
          <div>
            <label className="mb-1 block text-xs text-[var(--color-text-secondary)]">
              Query 参数模板 JSON
            </label>
            <Input.TextArea
              className="http-tool-form__json"
              rows={7}
              value={knowledgeDraft.queryTemplateJson}
              onChange={(event) => setKnowledgeDraft({ ...knowledgeDraft, queryTemplateJson: event.target.value })}
              placeholder={'{\n  "query": "{{query}}"\n}'}
              style={{ fontFamily: 'var(--font-mono)' }}
            />
          </div>
          <div>
            <label className="mb-1 block text-xs text-[var(--color-text-secondary)]">
              参数 Schema JSON
            </label>
            <Input.TextArea
              className="http-tool-form__json"
              rows={7}
              value={knowledgeDraft.parameterSchemaJson}
              onChange={(event) => setKnowledgeDraft({ ...knowledgeDraft, parameterSchemaJson: event.target.value })}
              placeholder={JSON.stringify(DEFAULT_HTTP_PARAMETER_SCHEMA, null, 2)}
              style={{ fontFamily: 'var(--font-mono)' }}
            />
          </div>
          </div>
          {sendsBody && (
            <div className="col-span-2">
              <label className="mb-1 block text-xs text-[var(--color-text-secondary)]">
                Body 请求体模板 JSON
              </label>
              <Input.TextArea
                className="http-tool-form__json"
                rows={7}
                value={knowledgeDraft.defaultPayloadJson}
                onChange={(event) => setKnowledgeDraft({ ...knowledgeDraft, defaultPayloadJson: event.target.value })}
                placeholder={'{\n  "query": "{{query}}"\n}'}
                style={{ fontFamily: 'var(--font-mono)' }}
              />
            </div>
          )}
          </div>
          {!sendsBody && (
            <div className="rounded-md bg-[var(--color-bg-secondary)] px-3 py-2 text-xs text-[var(--color-text-tertiary)]">
              当前请求类型不会发送 Body，请把需要传给接口的字段放到 Query 参数模板或 URL 模板中。
            </div>
          )}
        </div>
          );
        })()}
      </Modal>

      <Modal
        title="选择 Skills"
        open={skillsSelectorOpen}
        onCancel={() => setSkillsSelectorOpen(false)}
        onOk={() => setSkillsSelectorOpen(false)}
        okText="完成"
        cancelText="关闭"
        width={720}
      >
        {enabledSkills.length === 0 ? (
          <div className="py-8 text-center text-sm text-[var(--color-text-tertiary)]">
            暂无已安装并启用的 Skill。
          </div>
        ) : (
          <div className="max-h-[520px] space-y-2 overflow-y-auto pr-1">
            {enabledSkills.map((skill) => (
              <div
                key={skill.id}
                className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-primary)] px-4 py-3"
              >
                <Checkbox
                  checked={selectedSkillSet.has(skill.id)}
                  onChange={(event) => toggleSkill(skill.id, event.target.checked)}
                >
                  <span className="font-medium text-[var(--color-text-primary)]">
                    {skill.name}
                  </span>
                </Checkbox>
                <div className="mt-2 line-clamp-2 text-xs text-[var(--color-text-secondary)]">
                  {skill.description || skill.trigger || skill.id}
                </div>
                <div className="mt-2 flex max-h-7 flex-nowrap gap-2 overflow-hidden">
                  <Tag size="small" color="blue">
                    {skill.category}
                  </Tag>
                  {skill.tags.slice(0, 3).map((tag) => (
                    <Tag key={tag} size="small" color="mauve">
                      {tag}
                    </Tag>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </Modal>
    </div>
  );
}

function legacyModeFromProfile(profile: string): 'ask_every_time' | 'accept_edits' | 'bypass' {
  if (profile === 'ask_every_time') return 'ask_every_time';
  if (profile === 'trusted_tools' || profile === 'read_only') return 'bypass';
  return 'accept_edits';
}
