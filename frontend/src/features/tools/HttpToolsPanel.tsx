/**
 * HttpToolsPanel — HTTP 工具二级管理面板。
 *
 * 使用 Workbench 列表展示 HTTP 工具，编辑仍复用已有表单弹窗。
 */

import { useMemo, useState } from 'react';
import { Edit3, Globe2, Plus, Trash2 } from 'lucide-react';
import { Button, Tag } from '@/components/design';
import {
  WorkbenchEmptyPanel,
  WorkbenchList,
  WorkbenchListIcon,
  WorkbenchListRow,
  WorkbenchLoadingPanel,
  WorkbenchSection,
} from '../../components/workbench';
import { useHttpTools } from '../../hooks/useKnowledgeTools';
import type { HttpTool } from '../../types/tool';
import { HttpToolEditor } from './HttpToolEditor';
import { HttpToolFilters } from './HttpToolFilters';
import {
  EMPTY_HTTP_TOOL_DRAFT,
  draftToHttpToolPayload,
  httpToolToDraft,
  type HttpToolDraft,
} from './httpToolDraft';

export function HttpToolsPanel() {
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
    <section className="h-full min-h-0 overflow-y-auto bg-[var(--color-bg-primary)]">
      <div className="mx-auto w-full max-w-5xl px-5 py-5">
        <div className="mb-5 flex items-center justify-between gap-3">
          <HttpToolFilters
            query={query}
            loading={loading}
            onQueryChange={setQuery}
            onRefresh={() => void refresh()}
          />
          <Button type="primary" icon={<Plus size={14} />} onClick={() => openEditor()}>
            新建
          </Button>
        </div>

        <WorkbenchSection title="HTTP 工具" compact>
          {error ? (
            <WorkbenchEmptyPanel>
              <span className="text-[var(--color-error)]">{error}</span>
            </WorkbenchEmptyPanel>
          ) : loading ? (
            <WorkbenchLoadingPanel tip="加载 HTTP 工具..." />
          ) : filteredTools.length === 0 ? (
            <WorkbenchEmptyPanel onClick={() => openEditor()}>
              暂无 HTTP 工具 <span className="text-[var(--color-text-primary)]">新建</span>
            </WorkbenchEmptyPanel>
          ) : (
            <WorkbenchList>
              {filteredTools.map((tool, index) => (
                <HttpToolRow
                  key={tool.id}
                  tool={tool}
                  last={index === filteredTools.length - 1}
                  onEdit={() => openEditor(tool)}
                  onDelete={() => void deleteTool(tool.id)}
                />
              ))}
            </WorkbenchList>
          )}
        </WorkbenchSection>
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

function HttpToolRow({
  tool,
  last,
  onEdit,
  onDelete,
}: {
  tool: HttpTool;
  last: boolean;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const hasAuthorization = Object.keys(tool.headers || {}).some((key) => key.toLowerCase() === 'authorization');

  return (
    <WorkbenchListRow
      icon={(
        <WorkbenchListIcon>
          <Globe2 size={20} strokeWidth={1.8} />
        </WorkbenchListIcon>
      )}
      title={tool.name}
      subtitle={tool.url}
      description={tool.description || tool.parameterInstructions || '暂无说明'}
      last={last}
      layout="columns"
      onSelect={onEdit}
      meta={(
        <>
          <Tag size="small" color={tool.enabled ? 'green' : 'orange'}>{tool.enabled ? '启用' : '停用'}</Tag>
          <Tag size="small" color="blue">{tool.method}</Tag>
          <Tag size="small" color={tool.readonly ? 'green' : 'orange'}>{tool.readonly ? '只读' : '可写'}</Tag>
          {hasAuthorization ? <Tag size="small" color="transparent">Authorization</Tag> : null}
        </>
      )}
      trailing={(
        <>
          <button
            type="button"
            onClick={onEdit}
            className="icon-button h-8 w-8 rounded-md text-[var(--color-text-tertiary)] transition hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]"
            aria-label={`编辑 ${tool.name}`}
          >
            <Edit3 size={15} />
          </button>
          <button
            type="button"
            onClick={onDelete}
            className="icon-button h-8 w-8 rounded-md text-[var(--color-text-tertiary)] transition hover:bg-[var(--color-error-bg)] hover:text-[var(--color-error)]"
            aria-label={`删除 ${tool.name}`}
          >
            <Trash2 size={15} />
          </button>
        </>
      )}
    />
  );
}
