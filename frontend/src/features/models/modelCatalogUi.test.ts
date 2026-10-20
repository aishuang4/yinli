/** 模型管理展示 helper。 */

import { describe, expect, it } from 'vitest';
import {
  adapterLabel,
  catalogDraftDirty,
  isProviderConfigured,
  providerConnectionLabel,
  providerConnectionStatus,
  reviewConfigForSelection,
} from './modelCatalogUi';
import type { ModelCatalog, ModelProvider } from './useModelCatalog';

function provider(patch: Partial<ModelProvider> = {}): ModelProvider {
  return {
    id: 'p1',
    name: 'DeepSeek',
    adapter: 'deepseek',
    baseUrl: 'https://api.deepseek.com',
    enabled: true,
    requiresApiKey: true,
    apiKeyConfigured: true,
    ...patch,
  };
}

describe('adapterLabel', () => {
  it('把协议 id 转成可读名称', () => {
    expect(adapterLabel('dashscope')).toBe('通义千问');
    expect(adapterLabel('custom')).toBe('custom');
  });
});

describe('providerConnectionStatus', () => {
  it('按启用、Key、地址判断连接状态', () => {
    expect(providerConnectionStatus(provider({ enabled: false }))).toBe('disabled');
    expect(providerConnectionStatus(provider({ apiKeyConfigured: false, apiKey: '' }))).toBe('needs-key');
    expect(providerConnectionStatus(provider({ adapter: 'openai', baseUrl: '', requiresApiKey: false, apiKeyConfigured: false }))).toBe('needs-url');
    expect(providerConnectionStatus(provider())).toBe('ready');
  });

  it('已配置 Key 或无需 Key 且有地址才算已连接', () => {
    expect(isProviderConfigured(provider())).toBe(true);
    expect(isProviderConfigured(provider({ apiKeyConfigured: false }))).toBe(false);
  });

  it('状态文案面向用户', () => {
    expect(providerConnectionLabel('needs-key')).toBe('待填写 Key');
    expect(providerConnectionLabel('ready')).toBe('已连接');
  });
});

describe('catalogDraftDirty', () => {
  const catalog: ModelCatalog = {
    schemaVersion: 1,
    revision: 1,
    etag: '1',
    defaultModelId: 'm1',
    modelsPath: '/tmp/models.json',
    providers: [provider()],
    models: [{
      id: 'm1',
      providerId: 'p1',
      model: 'deepseek-v4-flash',
      label: 'Flash',
      enabled: true,
      fallbackModelIds: [],
      contextSize: 0,
      parameters: { temperature: 0.7, topP: 0.8, maxTokens: 4096 },
      capabilities: { images: false },
    }],
  };

  it('未改内容时不算脏', () => {
    expect(catalogDraftDirty(catalog, catalog)).toBe(false);
  });

  it('改默认模型时算脏', () => {
    expect(catalogDraftDirty({ ...catalog, defaultModelId: '' }, catalog)).toBe(true);
  });

  it('改审核模型时算脏，已通过的同一模型不重新探测', () => {
    const selected = reviewConfigForSelection('m1', null);
    expect(catalogDraftDirty({ ...catalog, permissionReview: selected }, catalog)).toBe(true);
    const passed = { ...selected!, probeStatus: 'passed' as const };
    expect(reviewConfigForSelection('m1', passed)).toEqual(passed);
    expect(reviewConfigForSelection('', passed)).toBeNull();
  });
});
