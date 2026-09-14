/** 模型和供应商连接管理：默认展示摘要，按需展开详细配置。 */
import { useState } from 'react';
import { ChevronDown, ChevronRight, Plus, RefreshCw, Save, Trash2 } from 'lucide-react';
import { TextField, PasswordField, SelectField, NumberField, ToggleField } from '../../components/form';
import { catalogRequest, modelAvailable, useModelCatalog, type ModelCatalog, type ModelEntry, type ModelProvider } from './useModelCatalog';
import { notifyError, notifySuccess } from '../../utils/feedback';

const iconClass = 'inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md hover:bg-[var(--color-bg-hover)] disabled:opacity-40';
const adapters = ['openai', 'deepseek', 'dashscope', 'moonshot', 'gemini', 'anthropic', 'ollama'];

export function ModelsManager() {
  const { catalog, error, loading, reload } = useModelCatalog(false);
  const openModelsFile = async () => {
    if (!catalog?.modelsPath) return;
    if (!window.electronAPI) {
      notifyError('请在桌面端打开 models.json');
      return;
    }
    const result = await window.electronAPI.openPath(catalog.modelsPath);
    if (!result.ok) notifyError(result.error || '无法打开 models.json');
  };
  return <section className="border-y border-[var(--color-border)] py-5">
    <header className="mb-4 flex items-start justify-between gap-4"><div><h2 className="text-base font-medium">模型管理</h2>{catalog?.modelsPath && <button type="button" className="mt-1 max-w-full truncate text-left text-xs text-[var(--color-text-tertiary)] underline decoration-dotted underline-offset-2 hover:text-[var(--color-text-primary)]" title="打开 models.json" onClick={() => void openModelsFile()}>{catalog.modelsPath}</button>}</div><button className={iconClass} aria-label="重新读取模型配置" title="重新读取" disabled={loading} onClick={() => void reload()}><RefreshCw size={16} /></button></header>
    {error && <p role="alert" className="mb-3 text-sm text-[var(--color-error)]">{error}</p>}
    {catalog ? <CatalogEditor key={catalog.etag} initial={catalog} onSaved={reload} /> : loading ? <p>加载中...</p> : null}
  </section>;
}

function CatalogEditor({ initial, onSaved }: { initial: ModelCatalog; onSaved: () => Promise<void> }) {
  const [draft, setDraft] = useState(initial);
  const isProviderConfigured = (item: ModelProvider) => Boolean(
    item.apiKeyConfigured || (!item.requiresApiKey && item.baseUrl.trim()),
  );
  const [expandedProviderId, setExpandedProviderId] = useState(
    initial.providers.find(item => isProviderConfigured(item))?.id || '',
  );
  const [expandedModelId, setExpandedModelId] = useState('');
  const [saving, setSaving] = useState(false); const [error, setError] = useState('');
  const provider = draft.providers.find(item => item.id === expandedProviderId);
  const modelsForProvider = (providerId: string) => draft.models.filter(item => item.providerId === providerId);
  const updateProvider = (patch: Partial<ModelProvider>) => setDraft(current => ({ ...current, providers: current.providers.map(item => item.id === expandedProviderId ? { ...item, ...patch } : item) }));
  const updateModel = (id: string, patch: Partial<ModelEntry>) => setDraft(current => ({ ...current, models: current.models.map(item => item.id === id ? { ...item, ...patch } : item) }));
  const save = async () => {
    setSaving(true);
    setError('');
    try {
      const payload = {
        schemaVersion: draft.schemaVersion,
        revision: draft.revision,
        defaultModelId: draft.defaultModelId,
        etag: draft.etag,
        providers: draft.providers.map((item) => {
          // apiKeyConfigured 是服务端派生标记，不回传。
          const payload: Partial<typeof item> = { ...item };
          delete payload.apiKeyConfigured;
          return payload;
        }),
        models: draft.models.filter((item) => item.label.trim() && item.model.trim()),
      };
      await catalogRequest({ method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      notifySuccess('模型配置已保存');
      window.dispatchEvent(new Event('agent-workstation:model-settings-updated'));
      await onSaved();
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : '保存失败';
      setError(message);
      notifyError(message);
    } finally {
      setSaving(false);
    }
  };
  const addProvider = () => { const id = crypto.randomUUID(); setDraft(current => ({ ...current, providers: [...current.providers, { id, name: '新供应商', adapter: 'openai', baseUrl: '', enabled: true, requiresApiKey: true, apiKeyConfigured: false }] })); setExpandedProviderId(id); setExpandedModelId(''); };
  const removeModels = (ids: Set<string>) => setDraft(current => ({ ...current, defaultModelId: ids.has(current.defaultModelId) ? '' : current.defaultModelId, models: current.models.filter(item => !ids.has(item.id)).map(item => ({ ...item, fallbackModelIds: item.fallbackModelIds.filter(id => !ids.has(id)) })) }));
  const removeProvider = () => { if (!provider) return; removeModels(new Set(draft.models.filter(m => m.providerId === provider.id).map(m => m.id))); setDraft(current => ({ ...current, providers: current.providers.filter(p => p.id !== provider.id) })); setExpandedProviderId(draft.providers.find(p => p.id !== provider.id)?.id || ''); setExpandedModelId(''); };
  const addModel = () => { if (!provider) return; const id = crypto.randomUUID(); setDraft(current => ({ ...current, models: [...current.models, { id, providerId: provider.id, model: '', label: '', enabled: true, fallbackModelIds: [], parameters: { temperature: 0.7, topP: 0.8, maxTokens: 4096 }, capabilities: { images: false } }] })); setExpandedModelId(id); };
  const visibleProviders = draft.providers.filter(item => isProviderConfigured(item) || item.id === expandedProviderId);
  return <fieldset disabled={saving} className="min-w-0 space-y-4">
    <SelectField label="新会话默认模型" aria-label="新会话默认模型" style={{ width: '100%', marginTop: 6 }} value={draft.defaultModelId || undefined} showSearch optionFilterProp="label" placeholder="选择默认模型" onChange={value => setDraft({ ...draft, defaultModelId: String(value) })} options={draft.models.filter(m => modelAvailable(draft, m)).map(m => ({ value: m.id, label: `${m.label} · ${draft.providers.find(p => p.id === m.providerId)?.name}` }))} />
    <div className="flex items-center justify-between border-b border-[var(--color-border)] pb-2"><span className="text-sm font-medium">已配置供应商</span><button type="button" className={iconClass} title="添加供应商" aria-label="添加供应商" onClick={addProvider}><Plus size={16} /></button></div>
    <div className="divide-y divide-[var(--color-border)] rounded-md border border-[var(--color-border)]">{visibleProviders.map(item => { const models = modelsForProvider(item.id); const open = item.id === expandedProviderId; return <div key={item.id}><button type="button" className="flex w-full items-center gap-2 px-3 py-3 text-left" onClick={() => { setExpandedProviderId(open ? '' : item.id); setExpandedModelId(''); }}>{open ? <ChevronDown size={16} /> : <ChevronRight size={16} />}<span className="font-medium">{item.name || '未命名供应商'}</span><span className="text-xs text-[var(--color-text-tertiary)]">{item.adapter} · {models.length} 个模型</span><span className={`ml-auto rounded-full px-2 py-0.5 text-xs ${item.apiKeyConfigured ? 'bg-emerald-500/12 text-emerald-600 dark:text-emerald-400' : 'bg-amber-500/12 text-amber-700 dark:text-amber-400'}`}>{item.apiKeyConfigured ? 'Key 已配置' : 'Key 未配置'}</span></button>{open && <div className="space-y-4 border-t border-[var(--color-border)] p-4"><div className="grid grid-cols-2 gap-3"><TextField label="供应商名称" aria-label="供应商名称" value={item.name} onChange={e => updateProvider({ name: e.target.value })} /><SelectField label="协议" aria-label="供应商协议" style={{ width: '100%' }} value={item.adapter} onChange={adapter => updateProvider({ adapter: String(adapter), requiresApiKey: adapter !== 'ollama' })} options={adapters.map(value => ({ value, label: value }))} /><TextField className="col-span-2" label="Base URL" aria-label="Base URL" value={item.baseUrl} onChange={e => updateProvider({ baseUrl: e.target.value })} /><PasswordField className="col-span-2" label="API Key" aria-label="API Key" autoComplete="new-password" value={item.apiKey ?? ''} placeholder={item.apiKeyConfigured ? '已配置' : '未配置'} onChange={e => updateProvider({ apiKey: e.target.value })} /></div><div className="flex flex-wrap items-center gap-4 text-xs"><ToggleField label="启用供应商" checked={item.enabled} onChange={enabled => updateProvider({ enabled })} /><ToggleField label="需要 API Key" checked={item.requiresApiKey} onChange={requiresApiKey => updateProvider({ requiresApiKey })} /><button type="button" className="text-[var(--color-error)]" onClick={() => updateProvider({ apiKey: '', apiKeyConfigured: false })}>清除 Key</button><button type="button" className="ml-auto text-[var(--color-error)]" onClick={removeProvider}>删除供应商</button></div><div className="flex items-center justify-between border-b border-[var(--color-border)] pb-2 text-sm"><span>模型</span><button type="button" className={iconClass} title="添加模型" aria-label="添加模型" onClick={addModel}><Plus size={16} /></button></div>{models.map(model => <ModelRow key={model.id} model={model} draft={draft} open={expandedModelId === model.id} onToggle={() => setExpandedModelId(expandedModelId === model.id ? '' : model.id)} onChange={patch => updateModel(model.id, patch)} onDelete={() => removeModels(new Set([model.id]))} />)}{models.length === 0 && <p className="text-sm text-[var(--color-text-tertiary)]">暂无已配置模型，请点击右侧加号添加。</p>}</div>}</div>; })}{visibleProviders.length === 0 && <p className="p-4 text-sm text-[var(--color-text-tertiary)]">暂无已配置供应商。点击上方加号开始配置。</p>}</div>
    {error && <p role="alert" className="text-sm text-[var(--color-error)]">{error}</p>}<div className="flex justify-end"><button type="button" disabled={saving} onClick={() => void save()} className="flex items-center gap-2 rounded-md bg-[var(--color-text-primary)] px-4 py-2 text-sm text-[var(--color-bg-primary)]"><Save size={15} />{saving ? '保存中...' : '保存配置'}</button></div>
  </fieldset>;
}

function ModelRow({ model, draft, open, onToggle, onChange, onDelete }: { model: ModelEntry; draft: ModelCatalog; open: boolean; onToggle: () => void; onChange: (patch: Partial<ModelEntry>) => void; onDelete: () => void }) {
  return <div className="border-b border-[var(--color-border)] last:border-b-0"><div className="flex items-center gap-2 py-3"><button type="button" className="flex min-w-0 flex-1 items-center gap-2 text-left" onClick={onToggle}>{open ? <ChevronDown size={15} /> : <ChevronRight size={15} />}<span className="truncate font-medium">{model.label}</span><span className="truncate text-xs text-[var(--color-text-tertiary)]">{model.model}</span></button><span className={`rounded-full px-2 py-0.5 text-xs ${model.enabled ? 'bg-emerald-500/12 text-emerald-600 dark:text-emerald-400' : 'bg-slate-500/12 text-slate-500 dark:text-slate-400'}`}>{model.enabled ? '模型已启用' : '模型已停用'}</span><button type="button" className={iconClass} title="删除模型" aria-label={`删除模型 ${model.label}`} onClick={onDelete}><Trash2 size={15} /></button></div>{open && <div className="space-y-3 pb-4"><div className="flex items-end gap-2"><TextField className="min-w-0 flex-1" label="显示名称" aria-label="模型显示名称" value={model.label} onChange={e => onChange({ label: e.target.value })} /><TextField className="min-w-0 flex-1" label="模型名称" aria-label="模型名称" value={model.model} onChange={e => onChange({ model: e.target.value })} /></div><div className="grid grid-cols-3 gap-3">{([['temperature', '温度', 0, 2, 0.1], ['topP', 'Top P', 0, 1, 0.05], ['maxTokens', '最大输出 Token', 1, undefined, 1]] as const).map(([key, label, min, max, step]) => <NumberField key={key} label={label} min={min} max={max} step={step} value={model.parameters[key]} onChange={value => onChange({ parameters: { ...model.parameters, [key]: Number(value) } })} />)}</div><SelectField aria-label={`${model.label} 降级模型`} mode="multiple" style={{ width: '100%' }} placeholder="降级模型（按选择顺序）" value={model.fallbackModelIds} onChange={ids => onChange({ fallbackModelIds: Array.isArray(ids) ? ids.map(String) : [] })} options={draft.models.filter(item => item.id !== model.id && item.label.trim() && item.model.trim()).map(item => ({ value: item.id, label: `${item.label} · ${draft.providers.find(p => p.id === item.providerId)?.name}` }))} /><div className="flex gap-4 text-xs"><ToggleField label="启用模型" checked={model.enabled} onChange={enabled => onChange({ enabled })} /><ToggleField label="支持图片" checked={model.capabilities.images} onChange={images => onChange({ capabilities: { images } })} /></div></div>}</div>;
}
