/** 统一模型目录 API；界面永远不读取已保存的 Key。 */
import { useCallback, useEffect, useState } from 'react';

export interface ModelProvider {
  id: string; name: string; adapter: string; baseUrl: string;
  apiKey?: string; apiKeyConfigured: boolean; requiresApiKey: boolean; enabled: boolean;
}
export interface ModelEntry {
  id: string; providerId: string; model: string; label: string; enabled: boolean;
  fallbackModelIds: string[];
  /** 上下文窗口（tokens）；0 表示按供应商默认，后端据此决定压缩阈值。 */
  contextSize?: number;
  parameters: { temperature: number; topP: number; maxTokens: number };
  capabilities: { images: boolean };
}
export interface PermissionReviewConfig {
  modelConfigId: string;
  timeoutSeconds: number;
  maxOutputTokens: number;
  schemaVersion: 1;
  promptVersion: 'permission-review-v1';
  probeStatus: 'missing' | 'passed';
}

export interface ModelCatalog {
  schemaVersion: number; revision: number; etag: string; defaultModelId: string; modelsPath: string;
  providers: ModelProvider[]; models: ModelEntry[];
  permissionReview?: PermissionReviewConfig | null;
}

export async function catalogRequest(init?: RequestInit): Promise<ModelCatalog> {
  const response = await fetch('/api/settings/models', init);
  const text = await response.text();
  let data: { detail?: unknown } = {};
  try {
    data = text ? JSON.parse(text) as { detail?: unknown } : {};
  } catch {
    throw new Error(response.ok ? '模型配置响应无法识别' : `模型配置操作失败（${response.status}）`);
  }
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '模型配置操作失败');
  return data as ModelCatalog;
}

export function modelAvailable(catalog: ModelCatalog, model: ModelEntry): boolean {
  if (!model.label.trim() || !model.model.trim()) return false;
  const visited = new Set<string>();
  const check = (entry: ModelEntry): boolean => {
    if (visited.has(entry.id)) return false;
    visited.add(entry.id);
    const provider = catalog.providers.find(item => item.id === entry.providerId);
    const ready = Boolean(entry.enabled && provider?.enabled
      && (provider.adapter === 'gemini' || provider.baseUrl)
      && (!provider.requiresApiKey || provider.apiKeyConfigured));
    const result = ready && entry.fallbackModelIds.every(id => {
      const fallback = catalog.models.find(item => item.id === id);
      return Boolean(fallback && check(fallback));
    });
    visited.delete(entry.id);
    return result;
  };
  return check(model);
}

export function useModelCatalog(refreshOnFocus = true) {
  const [catalog, setCatalog] = useState<ModelCatalog | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const reload = useCallback(async () => {
    setLoading(true);
    try { setCatalog(await catalogRequest()); setError(''); }
    catch (cause) { setError(cause instanceof Error ? cause.message : '模型目录读取失败'); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => {
    void reload();
    const refresh = () => { void reload(); };
    window.addEventListener('agent-workstation:model-settings-updated', refresh);
    if (refreshOnFocus) window.addEventListener('focus', refresh);
    return () => {
      window.removeEventListener('agent-workstation:model-settings-updated', refresh);
      window.removeEventListener('focus', refresh);
    };
  }, [reload, refreshOnFocus]);
  return { catalog, error, loading, reload };
}
