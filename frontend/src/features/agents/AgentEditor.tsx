/**
 * AgentEditor — Agent 创建/编辑表单
 *
 * 使用 Compound Component 模式组织多个表单区块：
 * - Identity: 名称、头像、描述、系统提示词
 * - ModelConfig: 模型选择、Fallback 链、温度参数
 * - Tools: 默认工具（锁定）+ MCP + Skills
 * - Guardrails: 安全边界
 * - Collaboration: 子 Agent 配置
 *
 * @see vercel-composition-patterns: architecture-compound-components
 */

import { useState, useEffect, useCallback, useMemo } from 'react';
import { Pencil, Plus, Save, Trash2, X } from 'lucide-react';
import {
  Alert,
  Button,
  Checkbox,
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
import { createEmptyAgentForm } from '../../types/agent';
import { useMcp } from '../../hooks/useMcp';
import { useEnabledSkills } from '../../hooks/useSkills';
import { useKnowledgeTools } from '../../hooks/useKnowledgeTools';
import type { KnowledgeTool } from '../../types/tool';

interface AgentEditorProps {
  /** 编辑的 Agent（null 表示创建模式） */
  agent: Agent | null;
  /** 可用模型列表 */
  models: {
    primary: ModelOption[];
    fallback: ModelOption[];
  } | null;
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
  const [editingKnowledgeTool, setEditingKnowledgeTool] = useState<KnowledgeTool | null>(null);
  const [knowledgeDraft, setKnowledgeDraft] = useState({
    name: 'KnowledgeBaseSearch',
    description: '检索外部知识库并返回命中文档片段。',
    url: 'http://localhost:8080/search/knowledgeBaseSearch',
    authorization: '',
    headersJson: '{}',
    defaultPayloadJson: JSON.stringify({
      query: '',
      filename_pattern: '',
      filterCategories: [],
      searchMode: '',
    }, null, 2),
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
  } = useKnowledgeTools();

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
        modelConfig: { ...agent.modelConfig },
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

  const openKnowledgeEditor = useCallback((tool: KnowledgeTool | null = null) => {
    setEditingKnowledgeTool(tool);
    setKnowledgeDraft({
      name: tool?.name ?? 'KnowledgeBaseSearch',
      description: tool?.description ?? '检索外部知识库并返回命中文档片段。',
      url: tool?.url ?? 'http://localhost:8080/search/knowledgeBaseSearch',
      authorization: tool?.authorization ?? '',
      headersJson: JSON.stringify(tool?.headers ?? {}, null, 2),
      defaultPayloadJson: JSON.stringify(tool?.defaultPayload ?? {
        query: '',
        filename_pattern: '',
        filterCategories: [],
        searchMode: '',
      }, null, 2),
      parameterInstructions: tool?.parameterInstructions ?? 'query 是检索问题；filename_pattern 用于按文件名过滤；filterCategories 是分类过滤数组；searchMode 是检索模式。',
      enabled: tool?.enabled ?? true,
    });
    setKnowledgeEditorOpen(true);
  }, []);

  const handleSaveKnowledgeTool = useCallback(async () => {
    try {
      const headers = JSON.parse(knowledgeDraft.headersJson || '{}');
      const defaultPayload = JSON.parse(knowledgeDraft.defaultPayloadJson || '{}');
      const payload = {
        name: knowledgeDraft.name,
        description: knowledgeDraft.description,
        url: knowledgeDraft.url,
        authorization: knowledgeDraft.authorization,
        headers,
        defaultPayload,
        parameterInstructions: knowledgeDraft.parameterInstructions,
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
      setError(err instanceof Error ? err.message : '保存知识库工具失败，请检查 JSON 格式');
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
        <div className="rounded-xl bg-[var(--color-bg-primary)] px-5 py-4 shadow-sm">
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
          <div className="grid min-h-full grid-cols-[minmax(0,1fr)_360px] gap-4">
            <div className="space-y-4">
        {error && (
          <Alert type="error" message={error} showIcon />
        )}

        <section className="rounded-xl bg-[var(--color-bg-primary)] p-4 shadow-sm">
          <div className="mb-4 flex items-center justify-between gap-3">
            <div>
              <h3 className="text-sm font-medium text-[var(--color-text-primary)]">
                身份信息
              </h3>
              <div className="mt-1 text-xs text-[var(--color-text-tertiary)]">
                定义 Agent 的名称、用途和行为边界。
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
            <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-primary)] p-3 transition-all duration-300 ease-out">
              <div className="flex items-center justify-between gap-4">
                <div className="min-w-0">
                  <div className="text-sm font-medium text-[var(--color-text-primary)]">
                    注入 Agent 记忆
                  </div>
                  <div className="mt-1 text-xs text-[var(--color-text-tertiary)]">
                    开启后，每次运行都会把该 Agent 的主记忆文件作为长期上下文注入。
                  </div>
                </div>
                <ToggleButton
                  checked={form.memoryEnabled}
                  checkedLabel="开启"
                  uncheckedLabel="关闭"
                  ariaLabel="注入 Agent 记忆"
                  onChange={(checked) => setForm({ ...form, memoryEnabled: checked })}
                />
              </div>
            </div>

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
                  <Button
                    loading={mcpServersLoading}
                    disabled={enabledMcpServers.length === 0}
                    onClick={() => setMcpSelectorOpen(true)}
                  >
                    选择 MCP
                  </Button>
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

            {/* 知识库工具 */}
            <div>
              <label className="block text-xs text-[var(--color-text-secondary)] mb-1">
                知识库 HTTP 工具
              </label>
              <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-primary)] p-3">
                <div className="flex items-center justify-between gap-3">
                  <div className="min-w-0 text-sm text-[var(--color-text-secondary)]">
                    {selectedKnowledgeTools.length > 0
                      ? `已选择 ${selectedKnowledgeTools.length} 个知识库工具`
                      : '未选择知识库工具'}
                  </div>
                  <div className="flex shrink-0 gap-2">
                    <Button
                      loading={knowledgeToolsLoading}
                      onClick={() => setKnowledgeSelectorOpen(true)}
                    >
                      选择工具
                    </Button>
                    <Button
                      type="primary"
                      icon={<Plus size={14} />}
                      onClick={() => openKnowledgeEditor(null)}
                    >
                      新增
                    </Button>
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
                    可以新增一个知识库 HTTP 工具，然后选择注入到当前 Agent。
                  </div>
                )}
              </div>
              <div className="mt-1 text-xs text-[var(--color-text-tertiary)]">
                已选择 {form.knowledgeTools.length} 个知识库工具
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
                  <Button
                    loading={skillsLoading}
                    disabled={enabledSkills.length === 0}
                    onClick={() => setSkillsSelectorOpen(true)}
                  >
                    选择 Skills
                  </Button>
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

            </div>

            <div className="space-y-4">
        <section className="rounded-xl bg-[var(--color-bg-primary)] p-4 shadow-sm">
          <div className="mb-4">
            <h3 className="text-sm font-medium text-[var(--color-text-primary)]">
              模型
            </h3>
            <div className="mt-1 text-xs text-[var(--color-text-tertiary)]">
              选择主模型、降级模型链和生成参数。
            </div>
          </div>
          <div className="space-y-3">
            {primaryModelOptions.length === 0 && (
              <Alert
                type="warning"
                message="当前系统设置中的供应商未配置可用模型。请先到系统设置为当前供应商配置 API Key 和模型。"
              />
            )}
            <div>
              <label className="block text-xs text-[var(--color-text-secondary)] mb-1">
                主模型
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
                        fallback: [...form.modelConfig.fallback, 'qwen-turbo'],
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
          </div>
        </section>

        <section className="rounded-xl bg-[var(--color-bg-primary)] p-4 shadow-sm">
          <div className="mb-4">
            <h3 className="text-sm font-medium text-[var(--color-text-primary)]">
              计划模型
            </h3>
            <div className="mt-1 text-xs text-[var(--color-text-tertiary)]">
              控制 Agent 在调用工具前如何制定、展示和修订任务计划。
            </div>
          </div>
          <div className="space-y-3">
            <div className="flex items-center justify-between gap-4 rounded-md border border-[var(--color-border)] bg-[var(--color-bg-primary)] px-3 py-2">
              <div className="min-w-0">
                <div className="text-sm text-[var(--color-text-primary)]">启用计划层</div>
                <div className="mt-1 text-xs text-[var(--color-text-tertiary)]">
                  开启后，Agent 会在复杂任务中先形成计划，再进入 ReAct 工具循环。
                </div>
              </div>
              <ToggleButton
                checked={form.planConfig.enabled}
                onChange={(checked) =>
                  setForm({
                    ...form,
                    planConfig: { ...form.planConfig, enabled: checked },
                  })
                }
                checkedLabel="开启"
                uncheckedLabel="关闭"
                ariaLabel="启用计划层"
              />
            </div>

            <div>
              <label className="block text-xs text-[var(--color-text-secondary)] mb-1">
                计划模式
              </label>
              <Select
                value={form.planConfig.mode}
                disabled={!form.planConfig.enabled}
                onChange={(value) =>
                  setForm({
                    ...form,
                    planConfig: { ...form.planConfig, mode: value },
                  })
                }
                options={[
                  { label: '轻量计划 - 简短 1-3 步', value: 'lightweight' },
                  { label: '结构化计划 - 默认推荐', value: 'structured' },
                  { label: '严格计划 - 强约束步骤状态', value: 'strict' },
                ]}
                style={{ width: '100%' }}
              />
            </div>

            <div>
              <label className="block text-xs text-[var(--color-text-secondary)] mb-1">
                最大计划步骤
              </label>
              <InputNumber
                min={1}
                max={20}
                value={form.planConfig.maxSteps}
                disabled={!form.planConfig.enabled}
                onChange={(value) =>
                  setForm({
                    ...form,
                    planConfig: { ...form.planConfig, maxSteps: Number(value ?? 1) },
                  })
                }
                style={{ width: 128 }}
              />
            </div>

            <div className="space-y-2">
              <div className="flex items-center justify-between gap-4 rounded-md border border-[var(--color-border)] bg-[var(--color-bg-primary)] px-3 py-2">
                <div className="text-sm text-[var(--color-text-primary)]">工具调用前必须先计划</div>
                <ToggleButton
                  checked={form.planConfig.requirePlanBeforeTools}
                  onChange={(checked) =>
                    setForm({
                      ...form,
                      planConfig: { ...form.planConfig, requirePlanBeforeTools: checked },
                    })
                  }
                  checkedLabel="开启"
                  uncheckedLabel="关闭"
                  ariaLabel="工具调用前必须先计划"
                />
              </div>
              <div className="flex items-center justify-between gap-4 rounded-md border border-[var(--color-border)] bg-[var(--color-bg-primary)] px-3 py-2">
                <div className="text-sm text-[var(--color-text-primary)]">执行计划前需要用户确认</div>
                <ToggleButton
                  checked={form.planConfig.requireUserApproval}
                  onChange={(checked) =>
                    setForm({
                      ...form,
                      planConfig: { ...form.planConfig, requireUserApproval: checked },
                    })
                  }
                  checkedLabel="开启"
                  uncheckedLabel="关闭"
                  ariaLabel="执行计划前需要用户确认"
                />
              </div>
              <div className="flex items-center justify-between gap-4 rounded-md border border-[var(--color-border)] bg-[var(--color-bg-primary)] px-3 py-2">
                <div className="text-sm text-[var(--color-text-primary)]">允许执行中修订计划</div>
                <ToggleButton
                  checked={form.planConfig.allowReplanning}
                  onChange={(checked) =>
                    setForm({
                      ...form,
                      planConfig: { ...form.planConfig, allowReplanning: checked },
                    })
                  }
                  checkedLabel="开启"
                  uncheckedLabel="关闭"
                  ariaLabel="允许执行中修订计划"
                />
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
                value={form.guardrails.toolPermissionMode}
                disabled={form.guardrails.humanApproval}
                onChange={(value) =>
                  setForm({
                    ...form,
                    guardrails: {
                      ...form.guardrails,
                      toolPermissionMode: value as 'ask_every_time' | 'accept_edits' | 'bypass',
                    },
                  })
                }
                options={[
                  { label: '常规开发放行 - 推荐', value: 'accept_edits' },
                  { label: '每次工具调用都确认', value: 'ask_every_time' },
                  { label: '完全信任并自动放行', value: 'bypass' },
                ]}
                style={{ width: '100%' }}
              />
              <div className="mt-1 text-xs text-[var(--color-text-tertiary)]">
                常规开发放行会减少 MCP 和文件工具的确认弹窗；完全信任适合沙箱环境。
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

        <section className="rounded-xl bg-[var(--color-bg-primary)] p-4 shadow-sm">
          <h3 className="mb-3 text-sm font-medium text-[var(--color-text-primary)]">
            协作
          </h3>
          <div>
            <label className="block text-xs text-[var(--color-text-secondary)] mb-1">
              最大调用深度
            </label>
            <InputNumber
              min={1}
              max={5}
              value={form.maxDepth}
              onChange={(value) => setForm({ ...form, maxDepth: Number(value ?? 1) })}
              style={{ width: 96 }}
            />
            <div className="text-xs text-[var(--color-text-tertiary)] mt-1">
              控制 Agent 调用子 Agent 的最大嵌套层级
            </div>
          </div>
        </section>
            </div>
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
        title="选择知识库 HTTP 工具"
        open={knowledgeSelectorOpen}
        onCancel={() => setKnowledgeSelectorOpen(false)}
        onOk={() => setKnowledgeSelectorOpen(false)}
        okText="完成"
        cancelText="关闭"
        width={760}
      >
        {knowledgeTools.length === 0 ? (
          <div className="py-8 text-center text-sm text-[var(--color-text-tertiary)]">
            暂无知识库工具。可以先点击新增，配置你的知识库检索接口。
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
                      aria-label="编辑知识库工具"
                    />
                    <Button
                      danger
                      type="text"
                      icon={<Trash2 size={14} />}
                      onClick={() => handleDeleteKnowledgeTool(tool.id)}
                      aria-label="删除知识库工具"
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
        title={editingKnowledgeTool ? '编辑知识库 HTTP 工具' : '新增知识库 HTTP 工具'}
        open={knowledgeEditorOpen}
        onCancel={() => setKnowledgeEditorOpen(false)}
        onOk={handleSaveKnowledgeTool}
        okText="保存"
        cancelText="取消"
        width={780}
      >
        <div className="grid grid-cols-2 gap-3">
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
              ariaLabel="启用知识库工具"
              onChange={(checked) => setKnowledgeDraft({ ...knowledgeDraft, enabled: checked })}
            />
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
          <div className="col-span-2">
            <label className="mb-1 block text-xs text-[var(--color-text-secondary)]">
              Authorization
            </label>
            <Input
              value={knowledgeDraft.authorization}
              onChange={(event) => setKnowledgeDraft({ ...knowledgeDraft, authorization: event.target.value })}
              placeholder="直接填写 Authorization header 的完整值"
            />
          </div>
          <div className="col-span-2">
            <label className="mb-1 block text-xs text-[var(--color-text-secondary)]">
              工具描述
            </label>
            <Input.TextArea
              rows={3}
              value={knowledgeDraft.description}
              onChange={(event) => setKnowledgeDraft({ ...knowledgeDraft, description: event.target.value })}
              placeholder="告诉模型什么时候应该调用这个知识库工具"
            />
          </div>
          <div className="col-span-2">
            <label className="mb-1 block text-xs text-[var(--color-text-secondary)]">
              参数说明
            </label>
            <Input.TextArea
              rows={3}
              value={knowledgeDraft.parameterInstructions}
              onChange={(event) => setKnowledgeDraft({ ...knowledgeDraft, parameterInstructions: event.target.value })}
              placeholder="说明 query、filename_pattern、filterCategories、searchMode 应该如何填写"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs text-[var(--color-text-secondary)]">
              Headers JSON
            </label>
            <Input.TextArea
              rows={7}
              value={knowledgeDraft.headersJson}
              onChange={(event) => setKnowledgeDraft({ ...knowledgeDraft, headersJson: event.target.value })}
              style={{ fontFamily: 'var(--font-mono)' }}
            />
          </div>
          <div>
            <label className="mb-1 block text-xs text-[var(--color-text-secondary)]">
              默认请求体 JSON
            </label>
            <Input.TextArea
              rows={7}
              value={knowledgeDraft.defaultPayloadJson}
              onChange={(event) => setKnowledgeDraft({ ...knowledgeDraft, defaultPayloadJson: event.target.value })}
              style={{ fontFamily: 'var(--font-mono)' }}
            />
          </div>
        </div>
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
