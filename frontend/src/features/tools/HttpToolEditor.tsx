/**
 * HttpToolEditor — HTTP 工具编辑弹窗。
 *
 * 统一维护 HTTP 工具草稿、JSON 字段布局和表单字段样式，供工具页和 Agent 编辑器复用。
 */

import { Modal } from '@agentscope-ai/design';
import {
  FormError,
  FormField,
  FormGrid,
  FormSection,
  SelectField,
  TextAreaField,
  TextField,
  ToggleField,
} from '../../components/form';
import {
  DEFAULT_PARAMETER_SCHEMA,
  type HttpToolDraft,
} from './httpToolDraft';
import type { HttpTool } from '../../types/tool';

const HTTP_METHOD_OPTIONS = [
  { label: 'GET - 查询读取', value: 'GET' },
  { label: 'POST - 提交/检索', value: 'POST' },
  { label: 'PUT - 整体更新', value: 'PUT' },
  { label: 'PATCH - 局部更新', value: 'PATCH' },
  { label: 'DELETE - 删除/撤销', value: 'DELETE' },
];

interface HttpToolEditorProps {
  open: boolean;
  editingTool: HttpTool | null;
  draft: HttpToolDraft;
  saving: boolean;
  error: string | null;
  onDraftChange: (draft: HttpToolDraft) => void;
  onCancel: () => void;
  onSave: () => void;
}

export function HttpToolEditor({
  open,
  editingTool,
  draft,
  saving,
  error,
  onDraftChange,
  onCancel,
  onSave,
}: HttpToolEditorProps) {
  const sendsBody = !['GET', 'DELETE'].includes(draft.method);
  const methodDescription = sendsBody
    ? '该请求会把 Body 模板作为 JSON 请求体发送；Query 模板会拼接到 URL 查询参数。'
    : '该请求不会发送 Body，参数会合并到 Query 模板并拼接到 URL 查询参数。';

  return (
    <Modal
      title={editingTool ? '编辑 HTTP 工具' : '新建 HTTP 工具'}
      open={open}
      onCancel={onCancel}
      onOk={onSave}
      okText="保存"
      cancelText="取消"
      confirmLoading={saving}
      width={840}
    >
      <div className="grid gap-3.5">
        <FormError>{error}</FormError>

        <FormSection title="基础信息">
          <FormGrid>
            <TextField
              label="工具名称"
              value={draft.name}
              onChange={(event) => onDraftChange({ ...draft, name: event.target.value })}
              placeholder="如：公司知识库检索"
            />
            <ToggleField
              label="是否启用"
              checked={draft.enabled}
              onChange={(enabled) => onDraftChange({ ...draft, enabled })}
            />
          </FormGrid>
          <TextAreaField
            className="mt-3"
            fieldClassName="min-h-[82px]"
            label="工具描述"
            value={draft.description}
            onChange={(event) => onDraftChange({ ...draft, description: event.target.value })}
            rows={3}
            placeholder="告诉模型什么时候应该调用这个 HTTP 工具"
          />
        </FormSection>

        <FormSection title="请求配置">
          <div className="grid gap-3 md:grid-cols-[200px_1fr]">
            <SelectField
              label="请求类型"
              value={draft.method}
              onChange={(value) => onDraftChange({ ...draft, method: value as HttpTool['method'] })}
              options={HTTP_METHOD_OPTIONS}
            />
            <FormField label="发送方式">
              <div className="min-h-[34px] rounded-md bg-[var(--color-bg-primary)] px-2.5 py-2 text-xs leading-[18px] text-[var(--color-text-secondary)]">
                {methodDescription}
              </div>
            </FormField>
          </div>
          <TextField
            className="mt-3"
            label="接口地址"
            value={draft.url}
            onChange={(event) => onDraftChange({ ...draft, url: event.target.value })}
            placeholder="http://localhost:8080/search/knowledgeBaseSearch"
          />
        </FormSection>

        <FormSection title="请求头">
          <TextAreaField
            fieldClassName="min-h-[160px] max-h-[220px] overflow-auto resize-y"
            label="Headers JSON"
            value={draft.headersJson}
            onChange={(event) => onDraftChange({ ...draft, headersJson: event.target.value })}
            rows={6}
            style={{ fontFamily: 'var(--font-mono)' }}
          />
        </FormSection>

        <FormSection title="参数配置">
          <TextAreaField
            className="mb-3"
            fieldClassName="min-h-[82px]"
            label="参数说明"
            value={draft.parameterInstructions}
            onChange={(event) => onDraftChange({ ...draft, parameterInstructions: event.target.value })}
            rows={3}
            placeholder="说明 query、filename_pattern、filterCategories、searchMode 应该如何填写"
          />
          <FormGrid>
            <TextAreaField
              fieldClassName="min-h-[160px] max-h-[220px] overflow-auto resize-y"
              label="Query 参数模板 JSON"
              value={draft.queryTemplateJson}
              onChange={(event) => onDraftChange({ ...draft, queryTemplateJson: event.target.value })}
              rows={6}
              placeholder={'{\n  "query": "{{query}}"\n}'}
              style={{ fontFamily: 'var(--font-mono)' }}
            />
            <TextAreaField
              fieldClassName="min-h-[160px] max-h-[220px] overflow-auto resize-y"
              label="参数 Schema JSON"
              value={draft.parameterSchemaJson}
              onChange={(event) => onDraftChange({ ...draft, parameterSchemaJson: event.target.value })}
              rows={6}
              placeholder={JSON.stringify(DEFAULT_PARAMETER_SCHEMA, null, 2)}
              style={{ fontFamily: 'var(--font-mono)' }}
            />
          </FormGrid>
          {sendsBody && (
            <TextAreaField
              className="mt-3"
              fieldClassName="min-h-[160px] max-h-[220px] overflow-auto resize-y"
              label="Body 请求体模板 JSON"
              value={draft.bodyTemplateJson}
              onChange={(event) => onDraftChange({ ...draft, bodyTemplateJson: event.target.value })}
              rows={6}
              placeholder={'{\n  "query": "{{query}}"\n}'}
              style={{ fontFamily: 'var(--font-mono)' }}
            />
          )}
        </FormSection>
      </div>
    </Modal>
  );
}
