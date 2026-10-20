/** 模型管理页的供应商/协议展示文案。 */

import type { ModelCatalog, ModelEntry, ModelProvider } from './useModelCatalog';

export const ADAPTER_OPTIONS = [
  { value: 'openai', label: 'OpenAI 兼容' },
  { value: 'deepseek', label: 'DeepSeek' },
  { value: 'dashscope', label: '通义千问' },
  { value: 'moonshot', label: 'Kimi' },
  { value: 'gemini', label: 'Gemini' },
  { value: 'anthropic', label: 'Claude' },
  { value: 'ollama', label: 'Ollama' },
] as const;

export function adapterLabel(adapter: string): string {
  return ADAPTER_OPTIONS.find((item) => item.value === adapter)?.label || adapter;
}

export type ProviderConnectionStatus = 'disabled' | 'needs-key' | 'needs-url' | 'ready';

export function providerConnectionStatus(provider: ModelProvider): ProviderConnectionStatus {
  if (!provider.enabled) return 'disabled';
  if (provider.requiresApiKey && !provider.apiKeyConfigured && !provider.apiKey) return 'needs-key';
  if (provider.adapter !== 'gemini' && !provider.baseUrl.trim()) return 'needs-url';
  return 'ready';
}

export function providerConnectionLabel(status: ProviderConnectionStatus): string {
  if (status === 'disabled') return '已停用';
  if (status === 'needs-key') return '待填写 Key';
  if (status === 'needs-url') return '待填写地址';
  return '已连接';
}

export function isProviderConfigured(provider: ModelProvider): boolean {
  return Boolean(provider.apiKeyConfigured || (!provider.requiresApiKey && provider.baseUrl.trim()));
}

export function catalogDraftDirty(draft: ModelCatalog, initial: ModelCatalog): boolean {
  return JSON.stringify(serializeCatalogDraft(draft)) !== JSON.stringify(serializeCatalogDraft(initial));
}

function serializeCatalogDraft(catalog: ModelCatalog) {
  return {
    defaultModelId: catalog.defaultModelId,
    permissionReview: catalog.permissionReview ?? null,
    providers: catalog.providers.map((item) => ({
      id: item.id,
      name: item.name,
      adapter: item.adapter,
      baseUrl: item.baseUrl,
      enabled: item.enabled,
      requiresApiKey: item.requiresApiKey,
      apiKey: item.apiKey || '',
    })),
    models: catalog.models.map((item) => ({
      id: item.id,
      providerId: item.providerId,
      model: item.model,
      label: item.label,
      enabled: item.enabled,
      fallbackModelIds: item.fallbackModelIds,
      contextSize: item.contextSize ?? 0,
      parameters: item.parameters,
      capabilities: item.capabilities,
    })),
  };
}

export function emptyProvider(): ModelProvider {
  return {
    id: crypto.randomUUID(),
    name: '新供应商',
    adapter: 'openai',
    baseUrl: '',
    enabled: true,
    requiresApiKey: true,
    apiKeyConfigured: false,
  };
}

export function emptyModel(providerId: string): ModelEntry {
  return {
    id: crypto.randomUUID(),
    providerId,
    model: '',
    label: '',
    enabled: true,
    fallbackModelIds: [],
    contextSize: 0,
    parameters: { temperature: 0.7, topP: 0.8, maxTokens: 4096 },
    capabilities: { images: false },
  };
}
