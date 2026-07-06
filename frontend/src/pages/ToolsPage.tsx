import { useMemo, useState } from 'react';
import { Edit3, Globe2, Plus, Plug, RefreshCw, Settings2, Trash2, Wrench } from 'lucide-react';
import { Button, Spinner, Tag } from '@agentscope-ai/design';
import { HttpToolEditor } from '../features/tools/HttpToolEditor';
import { HttpToolFilters } from '../features/tools/HttpToolFilters';
import {
  EMPTY_HTTP_TOOL_DRAFT,
  draftToHttpToolPayload,
  httpToolToDraft,
  type HttpToolDraft,
} from '../features/tools/httpToolDraft';
import { MCPPage } from './MCPPage';
import { useHttpTools, useSystemTools } from '../hooks/useKnowledgeTools';
import type { HttpTool } from '../types/tool';

type ToolsTab = 'mcp' | 'http' | 'system';

const TOOL_TABS: Array<{ key: ToolsTab; label: string; description: string; icon: typeof Plug }> = [
  { key: 'mcp', label: 'MCP', description: '协议化工具服务器', icon: Plug },
  { key: 'http', label: 'HTTP 工具', description: '传统系统 API 快速封装', icon: Globe2 },
  { key: 'system', label: '系统工具', description: '内置文件、命令和人工交互工具', icon: Settings2 },
];

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
        <HttpToolFilters
          query={query}
          loading={loading}
          onQueryChange={setQuery}
          onRefresh={refresh}
        />
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

      <HttpToolEditor
        open={editorOpen}
        editingTool={editingTool}
        draft={draft}
        saving={saving}
        error={formError}
        onDraftChange={setDraft}
        onCancel={() => setEditorOpen(false)}
        onSave={() => void saveDraft()}
      />
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
