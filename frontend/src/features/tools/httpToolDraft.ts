/**
 * HTTP 工具草稿转换工具。
 *
 * 与 UI 组件分离，避免表单弹窗文件同时导出非组件对象。
 */

import type { HttpTool, HttpToolPayload } from '../../types/tool';

export interface HttpToolDraft {
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

export const DEFAULT_PARAMETER_SCHEMA = {
  type: 'object',
  properties: {
    query: { type: 'string', description: '检索问题或 API 查询参数。' },
    filename_pattern: { type: 'string', description: '可选文件名过滤。' },
    filterCategories: { type: 'array', items: { type: 'string' }, description: '可选分类过滤。' },
    searchMode: { type: 'string', description: '可选检索模式。' },
  },
};

export const EMPTY_HTTP_TOOL_DRAFT: HttpToolDraft = {
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

export function httpToolToDraft(tool: HttpTool | null): HttpToolDraft {
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

export function draftToHttpToolPayload(draft: HttpToolDraft): HttpToolPayload {
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
