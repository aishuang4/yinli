import { useMemo, useState } from 'react';
import { Edit3, Globe2, Plus, Plug, RefreshCw, Settings2, Trash2, Wrench } from 'lucide-react';
import { Button, Input, Modal, Select, Spinner, Tag } from '@agentscope-ai/design';
import { MCPPage } from './MCPPage';
import { useHttpTools, useSystemTools } from '../hooks/useKnowledgeTools';
import type { HttpTool, HttpToolPayload } from '../types/tool';

type ToolsTab = 'mcp' | 'http' | 'system';

const TOOL_TABS: Array<{ key: ToolsTab; label: string; description: string; icon: typeof Plug }> = [
  { key: 'mcp', label: 'MCP', description: '协议化工具服务器', icon: Plug },
  { key: 'http', label: 'HTTP 工具', description: '传统系统 API 快速封装', icon: Globe2 },
  { key: 'system', label: '系统工具', description: '内置文件、命令和人工交互工具', icon: Settings2 },
];

interface HttpToolDraft {
  name: string;
  description: string;
  method: HttpTool['method'];
  url: string;
  headersJson: string;
  queryTemplateJson: string;
  bodyTemplateJson: string;
  parameterSchemaJson: string;
  parameterInstructions: string;
  enabled: boolean;
}

const DEFAULT_PARAMETER_SCHEMA = {
  type: 'object',
  properties: {
    query: { type: 'string', description: '检索问题或 API 查询参数。' },
    filename_pattern: { type: 'string', description: '可选文件名过滤。' },
    filterCategories: { type: 'array', items: { type: 'string' }, description: '可选分类过滤。' },
    searchMode: { type: 'string', description: '可选检索模式。' },
  },
};

const EMPTY_HTTP_TOOL_DRAFT: HttpToolDraft = {
  name: 'KnowledgeBaseSearch',
  description: '检索外部知识库并返回命中文档片段。',
  method: 'POST',
  url: 'http://localhost:8080/search/knowledgeBaseSearch',
  headersJson: '{}',
  queryTemplateJson: '{}',
  bodyTemplateJson: JSON.stringify({
    query: '',
    filename_pattern: '',
    filterCategories: [],
    searchMode: '',
  }, null, 2),
  parameterSchemaJson: JSON.stringify(DEFAULT_PARAMETER_SCHEMA, null, 2),
  parameterInstructions: 'query 是检索问题；filename_pattern 用于按文件名过滤；filterCategories 是分类过滤数组；searchMode 是检索模式。',
  enabled: true,
};

function httpToolToDraft(tool: HttpTool | null): HttpToolDraft {
  if (!tool) return { ...EMPTY_HTTP_TOOL_DRAFT };
  return {
    name: tool.name,
    description: tool.description,
    method: tool.method,
    url: tool.url,
    headersJson: JSON.stringify(tool.headers ?? {}, null, 2),
    queryTemplateJson: JSON.stringify(tool.queryTemplate ?? {}, null, 2),
    bodyTemplateJson: JSON.stringify(tool.bodyTemplate ?? {}, null, 2),
    parameterSchemaJson: JSON.stringify(tool.parameterSchema ?? DEFAULT_PARAMETER_SCHEMA, null, 2),
    parameterInstructions: tool.parameterInstructions,
    enabled: tool.enabled,
  };
}

function draftToHttpToolPayload(draft: HttpToolDraft): HttpToolPayload {
  const queryTemplate = JSON.parse(draft.queryTemplateJson || '{}');
  const bodyTemplate = JSON.parse(draft.bodyTemplateJson || '{}');
  const parameterSchema = JSON.parse(draft.parameterSchemaJson || '{}');
  const sendsBody = !['GET', 'DELETE'].includes(draft.method);
  return {
    name: draft.name.trim(),
    description: draft.description.trim(),
    category: 'custom',
    method: draft.method,
    url: draft.url.trim(),
    authType: 'none',
    authConfig: {},
    headers: JSON.parse(draft.headersJson || '{}'),
    queryTemplate,
    bodyTemplate: sendsBody ? bodyTemplate : {},
    parameterSchema,
    parameterInstructions: draft.parameterInstructions.trim(),
    responseMode: 'full',
    responsePath: '',
    readonly: true,
    approvalPolicy: 'inherit',
    enabled: draft.enabled,
  };
}

export function ToolsPage() {
  const [activeTab, setActiveTab] = useState<ToolsTab>('mcp');
  const activeTabInfo = TOOL_TABS.find((item) => item.key === activeTab) || TOOL_TABS[0];
  const ActiveIcon = activeTabInfo?.icon || Wrench;

  return (
    <div className="grid h-full min-h-0 grid-rows-[auto_1fr] overflow-hidden bg-[var(--color-bg-primary)]">
      <header className="px-5 py-4">
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2">
              <ActiveIcon size={20} color="var(--color-accent)" />
              <h1 className="text-lg font-semibold text-[var(--color-text-primary)]">{activeTabInfo.label}</h1>
            </div>
            <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
              {activeTabInfo.description}
            </p>
          </div>
        </div>
        <div className="mt-4 grid grid-cols-3 gap-3">
          {TOOL_TABS.map((item) => {
            const Icon = item.icon;
            const active = activeTab === item.key;
            return (
              <button
                key={item.key}
                type="button"
                onClick={() => setActiveTab(item.key)}
                className={[
                  'ui-card-transition rounded-md p-3 text-left',
                  active
                    ? 'bg-[var(--color-accent-bg)] text-[var(--color-accent)]'
                    : 'bg-[var(--color-bg-secondary)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]',
                ].join(' ')}
              >
                <div className="flex items-center gap-2 text-sm font-medium">
                  <Icon size={15} />
                  <span>{item.label}</span>
                </div>
                <div className="mt-1 text-xs opacity-80">{item.description}</div>
              </button>
            );
          })}
        </div>
      </header>

      <main className="min-h-0 overflow-hidden">
        {activeTab === 'mcp' && <MCPPage embedded />}
        {activeTab === 'http' && <HttpToolsPanel />}
        {activeTab === 'system' && <SystemToolsPanel />}
      </main>
    </div>
  );
}

function HttpToolsPanel() {
  const { tools, loading, error, refresh, createTool, updateTool, deleteTool } = useHttpTools();
  const [query, setQuery] = useState('');
  const [editorOpen, setEditorOpen] = useState(false);
  const [editingTool, setEditingTool] = useState<HttpTool | null>(null);
  const [draft, setDraft] = useState<HttpToolDraft>(EMPTY_HTTP_TOOL_DRAFT);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const sendsBody = !['GET', 'DELETE'].includes(draft.method);
  const methodDescription = sendsBody
    ? '该请求会把 Body 模板作为 JSON 请求体发送；Query 模板会拼接到 URL 查询参数。'
    : '该请求不会发送 Body，参数会合并到 Query 模板并拼接到 URL 查询参数。';

  const filteredTools = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!normalized) return tools;
    return tools.filter((tool) => [
      tool.name,
      tool.description,
      tool.url,
      tool.parameterInstructions,
    ].some((value) => value.toLowerCase().includes(normalized)));
  }, [query, tools]);

  function openEditor(tool: HttpTool | null = null) {
    setEditingTool(tool);
    setDraft(httpToolToDraft(tool));
    setFormError(null);
    setEditorOpen(true);
  }

  async function saveDraft() {
    if (!draft.name.trim() || !draft.url.trim()) {
      setFormError('请输入工具名称和 URL');
      return;
    }
    try {
      setSaving(true);
      setFormError(null);
      const payload = draftToHttpToolPayload(draft);
      if (editingTool) {
        await updateTool(editingTool.id, payload);
      } else {
        await createTool(payload);
      }
      setEditorOpen(false);
    } catch (err) {
      setFormError(err instanceof Error ? err.message : '保存 HTTP 工具失败，请检查 JSON 格式');
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="grid h-full min-h-0 grid-rows-[auto_1fr] px-5 pb-5">
      <div className="flex items-center justify-between gap-3 py-3">
        <div className="grid flex-1 grid-cols-[minmax(240px,1fr)_auto] gap-3">
          <Input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索 HTTP 工具、URL 或说明" allowClear />
          <Button icon={<RefreshCw size={14} />} onClick={refresh} loading={loading}>刷新</Button>
        </div>
        <Button type="primary" onClick={() => openEditor()}>
          <Plus size={14} />
          新建 HTTP 工具
        </Button>
      </div>

      <div className="min-h-0 overflow-y-auto">
        {error ? (
          <div className="rounded-md border border-[var(--color-error)] p-4 text-sm text-[var(--color-error)]">{error}</div>
        ) : loading ? (
          <div className="flex justify-center py-12"><Spinner tip="加载 HTTP 工具..." /></div>
        ) : filteredTools.length === 0 ? (
          <ToolEmptyState
            icon={Globe2}
            title="暂无 HTTP 工具"
            description="新建一个 HTTP 检索工具，把传统系统 API 封装给 Agent 使用。"
          />
        ) : (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
            {filteredTools.map((tool) => (
              <div key={tool.id} className="grid h-[300px] grid-rows-[auto_1fr_auto] rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-4">
                <div>
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <h2 className="truncate text-sm font-semibold text-[var(--color-text-primary)]">{tool.name}</h2>
                      <div className="mt-1 truncate text-xs text-[var(--color-text-tertiary)]">{tool.url}</div>
                    </div>
                    <Tag size="small" color={tool.enabled ? 'green' : 'orange'}>{tool.enabled ? '启用' : '停用'}</Tag>
                  </div>
                  <div className="mt-3 flex flex-wrap gap-1">
                    <Tag size="small" color="blue">{tool.method}</Tag>
                    <Tag size="small" color="green">只读检索</Tag>
                    {Object.keys(tool.headers || {}).some((key) => key.toLowerCase() === 'authorization') && <Tag size="small" color="transparent">Authorization</Tag>}
                  </div>
                </div>
                <p className="mt-3 line-clamp-5 text-xs text-[var(--color-text-secondary)]">
                  {tool.description || tool.parameterInstructions || '暂无说明'}
                </p>
                <div className="mt-4 flex items-center justify-between gap-2">
                  <Button size="small" onClick={() => openEditor(tool)}>
                    <Edit3 size={13} />
                    编辑
                  </Button>
                  <Button size="small" onClick={() => void deleteTool(tool.id)}>
                    <Trash2 size={13} />
                    删除
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <Modal
        title={editingTool ? '编辑 HTTP 工具' : '新建 HTTP 工具'}
        open={editorOpen}
        onCancel={() => setEditorOpen(false)}
        onOk={() => void saveDraft()}
        okText="保存"
        cancelText="取消"
        confirmLoading={saving}
        width={840}
      >
        <div className="http-tool-form">
          {formError && <div className="rounded-md border border-[var(--color-error)] p-3 text-sm text-[var(--color-error)]">{formError}</div>}
          <div className="http-tool-form__section">
            <div className="http-tool-form__section-title">基础信息</div>
            <div className="http-tool-form__grid">
            <div>
              <label className="mb-1 block text-xs text-[var(--color-text-secondary)]">工具名称</label>
              <Input value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} placeholder="如：公司知识库检索" />
            </div>
            <div>
              <label className="mb-1 block text-xs text-[var(--color-text-secondary)]">是否启用</label>
              <Button className="http-tool-form__control" type={draft.enabled ? 'primary' : 'default'} onClick={() => setDraft({ ...draft, enabled: !draft.enabled })}>
                {draft.enabled ? '启用' : '停用'}
              </Button>
            </div>
          </div>
            <div className="mt-3">
              <label className="mb-1 block text-xs text-[var(--color-text-secondary)]">工具描述</label>
              <Input.TextArea className="http-tool-form__text" value={draft.description} onChange={(event) => setDraft({ ...draft, description: event.target.value })} rows={3} placeholder="告诉模型什么时候应该调用这个 HTTP 工具" />
            </div>
          </div>

          <div className="http-tool-form__section">
            <div className="http-tool-form__section-title">请求配置</div>
            <div className="grid grid-cols-[200px_1fr] gap-3">
            <div>
              <label className="mb-1 block text-xs text-[var(--color-text-secondary)]">请求类型</label>
              <Select
                value={draft.method}
                onChange={(value) => setDraft({ ...draft, method: value as HttpTool['method'] })}
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
              <label className="mb-1 block text-xs text-[var(--color-text-secondary)]">发送方式</label>
              <div className="http-tool-form__hint">
                {methodDescription}
              </div>
            </div>
          </div>
            <div className="mt-3">
            <label className="mb-1 block text-xs text-[var(--color-text-secondary)]">接口地址</label>
            <Input value={draft.url} onChange={(event) => setDraft({ ...draft, url: event.target.value })} placeholder="http://localhost:8080/search/knowledgeBaseSearch" />
          </div>
          </div>

          <div className="http-tool-form__section">
            <div className="http-tool-form__section-title">请求头</div>
            <div>
              <label className="mb-1 block text-xs text-[var(--color-text-secondary)]">Headers JSON</label>
              <Input.TextArea
                className="http-tool-form__json"
                value={draft.headersJson}
                onChange={(event) => setDraft({ ...draft, headersJson: event.target.value })}
                rows={6}
                style={{ fontFamily: 'var(--font-mono)' }}
              />
            </div>
          </div>

          <div className="http-tool-form__section">
            <div className="http-tool-form__section-title">参数配置</div>
            <div className="mb-3">
              <label className="mb-1 block text-xs text-[var(--color-text-secondary)]">参数说明</label>
              <Input.TextArea
                className="http-tool-form__text"
                value={draft.parameterInstructions}
                onChange={(event) => setDraft({ ...draft, parameterInstructions: event.target.value })}
                rows={3}
                placeholder="说明 query、filename_pattern、filterCategories、searchMode 应该如何填写"
              />
            </div>
            <div className="http-tool-form__grid">
            <div>
              <label className="mb-1 block text-xs text-[var(--color-text-secondary)]">Query 参数模板 JSON</label>
              <Input.TextArea
                className="http-tool-form__json"
                value={draft.queryTemplateJson}
                onChange={(event) => setDraft({ ...draft, queryTemplateJson: event.target.value })}
                rows={6}
                placeholder={'{\n  "query": "{{query}}"\n}'}
                style={{ fontFamily: 'var(--font-mono)' }}
              />
            </div>
            <div>
              <label className="mb-1 block text-xs text-[var(--color-text-secondary)]">参数 Schema JSON</label>
              <Input.TextArea
                className="http-tool-form__json"
                value={draft.parameterSchemaJson}
                onChange={(event) => setDraft({ ...draft, parameterSchemaJson: event.target.value })}
                rows={6}
                placeholder={JSON.stringify(DEFAULT_PARAMETER_SCHEMA, null, 2)}
                style={{ fontFamily: 'var(--font-mono)' }}
              />
            </div>
          </div>
          {sendsBody && (
            <div className="mt-3">
              <label className="mb-1 block text-xs text-[var(--color-text-secondary)]">Body 请求体模板 JSON</label>
              <Input.TextArea
                className="http-tool-form__json"
                value={draft.bodyTemplateJson}
                onChange={(event) => setDraft({ ...draft, bodyTemplateJson: event.target.value })}
                rows={6}
                placeholder={'{\n  "query": "{{query}}"\n}'}
                style={{ fontFamily: 'var(--font-mono)' }}
              />
            </div>
          )}
          </div>

        </div>
      </Modal>
    </section>
  );
}

function ToolEmptyState({
  icon: Icon,
  title,
  description,
}: {
  icon: typeof Globe2;
  title: string;
  description: string;
}) {
  return (
    <div className="flex min-h-[420px] items-center justify-center">
      <div className="text-center">
        <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-xl bg-[var(--color-bg-secondary)] text-[var(--color-text-tertiary)]">
          <Icon size={24} strokeWidth={1.5} />
        </div>
        <div className="mt-3 text-sm font-medium text-[var(--color-text-primary)]">{title}</div>
        <div className="mt-1 max-w-sm text-xs text-[var(--color-text-tertiary)]">{description}</div>
      </div>
    </div>
  );
}

function SystemToolsPanel() {
  const { tools, loading, error, refresh } = useSystemTools();

  return (
    <section className="grid h-full min-h-0 grid-rows-[auto_1fr] px-5 pb-5">
      <div className="flex items-center justify-between gap-3 py-3">
        <div className="text-sm text-[var(--color-text-secondary)]">
          系统工具由运行时内置，当前用于展示能力、风险和默认注入策略。
        </div>
        <Button icon={<RefreshCw size={14} />} onClick={refresh} loading={loading}>刷新</Button>
      </div>
      <div className="min-h-0 overflow-y-auto">
        {error ? (
          <div className="rounded-md border border-[var(--color-error)] p-4 text-sm text-[var(--color-error)]">{error}</div>
        ) : loading ? (
          <div className="flex justify-center py-12"><Spinner tip="加载系统工具..." /></div>
        ) : (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
            {tools.map((tool) => (
              <div key={tool.name} className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-4">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <h2 className="text-sm font-semibold text-[var(--color-text-primary)]">{tool.name}</h2>
                    <div className="mt-1 text-xs text-[var(--color-text-tertiary)]">{tool.category}</div>
                  </div>
                  <Tag size="small" color={tool.risk === 'high' ? 'red' : tool.risk === 'medium' ? 'orange' : 'green'}>{tool.risk}</Tag>
                </div>
                <p className="mt-3 line-clamp-3 text-xs text-[var(--color-text-secondary)]">{tool.description}</p>
                <div className="mt-3 flex flex-wrap gap-1">
                  <Tag size="small" color="transparent">{tool.action}</Tag>
                  <Tag size="small" color={tool.readonly ? 'green' : 'orange'}>{tool.readonly ? '只读' : '可写/执行'}</Tag>
                  <Tag size="small" color={tool.defaultEnabled ? 'blue' : 'transparent'}>{tool.defaultEnabled ? '默认注入' : '默认关闭'}</Tag>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
