/** 模型和供应商连接管理：顶栏保存，下方供应商列表滚动。 */

import { useState } from 'react';
import { Cpu, FileJson, Plus, RefreshCw, Save } from 'lucide-react';
import { Button, Tag } from '@/components/design';
import { SelectField } from '../../components/form';
import { ModelProviderEditor } from './ModelProviderEditor';
import {
  catalogDraftDirty,
  emptyModel,
  emptyProvider,
  isProviderConfigured,
} from './modelCatalogUi';
import { catalogRequest, modelAvailable, useModelCatalog, type ModelCatalog, type ModelEntry, type ModelProvider } from './useModelCatalog';
import { notifyError, notifySuccess } from '../../utils/feedback';

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

  if (!catalog) {
    return (
      <div className="flex h-full min-h-0 flex-col">
        <ModelsToolbar loading={loading} onReload={() => void reload()} />
        <div className="min-h-0 flex-1 overflow-auto px-8 py-6">
          <div className="mx-auto max-w-5xl">
            {error ? <p role="alert" className="text-sm text-[var(--color-error)]">{error}</p> : null}
            {loading ? <p className="text-sm text-[var(--color-text-tertiary)]">正在加载模型配置…</p> : null}
          </div>
        </div>
      </div>
    );
  }

  return (
    <CatalogEditor
      key={catalog.etag}
      initial={catalog}
      loading={loading}
      loadError={error}
      onSaved={reload}
      onReload={reload}
      onOpenFile={() => void openModelsFile()}
    />
  );
}

function ModelsToolbar({
  loading,
  dirty,
  saving,
  modelsPath,
  onReload,
  onOpenFile,
  onSave,
}: {
  loading: boolean;
  dirty?: boolean;
  saving?: boolean;
  modelsPath?: string;
  onReload: () => void;
  onOpenFile?: () => void;
  onSave?: () => void;
}) {
  return (
    <header className="shrink-0 border-b border-[var(--color-border)] bg-[var(--color-bg-primary)] px-8 py-4">
      <div className="mx-auto flex max-w-5xl items-center justify-between gap-4">
        <div className="flex min-w-0 items-center gap-2">
          <h1 className="text-2xl font-semibold text-[var(--color-text-primary)]">模型</h1>
          <Cpu size={18} className="shrink-0 text-[var(--color-text-tertiary)]" />
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {dirty ? <Tag size="small" color="orange" style={{ marginInlineEnd: 0 }}>有未保存的更改</Tag> : null}
          {modelsPath && onOpenFile ? (
            <button
              type="button"
              className="inline-flex h-8 w-8 items-center justify-center rounded-md text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]"
              title="打开配置文件"
              aria-label="打开配置文件"
              onClick={onOpenFile}
            >
              <FileJson size={16} />
            </button>
          ) : null}
          <button
            type="button"
            className="inline-flex h-8 w-8 items-center justify-center rounded-md text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)] disabled:opacity-40"
            aria-label="重新读取模型配置"
            title="重新读取"
            disabled={loading || saving}
            onClick={onReload}
          >
            <RefreshCw size={16} />
          </button>
          {onSave ? (
            <Button type="primary" icon={<Save size={15} />} loading={saving} onClick={onSave}>
              {saving ? '保存中…' : '保存配置'}
            </Button>
          ) : null}
        </div>
      </div>
    </header>
  );
}

function CatalogEditor({
  initial,
  loading,
  loadError,
  onSaved,
  onReload,
  onOpenFile,
}: {
  initial: ModelCatalog;
  loading: boolean;
  loadError: string;
  onSaved: () => Promise<void>;
  onReload: () => Promise<void>;
  onOpenFile: () => void;
}) {
  const [draft, setDraft] = useState(initial);
  const [expandedProviderId, setExpandedProviderId] = useState(
    initial.providers.find((item) => item.apiKeyConfigured || (!item.requiresApiKey && item.baseUrl.trim()))?.id
      || initial.providers[0]?.id
      || '',
  );
  const [expandedModelId, setExpandedModelId] = useState('');
  const [showAllProviders, setShowAllProviders] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const dirty = catalogDraftDirty(draft, initial);
  const availableModels = draft.models.filter((item) => modelAvailable(draft, item));
  const defaultModel = draft.models.find((item) => item.id === draft.defaultModelId);
  const visibleProviders = draft.providers.filter((item) => (
    showAllProviders
    || isProviderConfigured(item)
    || item.id === expandedProviderId
  ));
  const hiddenProviderCount = draft.providers.length - draft.providers.filter((item) => (
    isProviderConfigured(item) || item.id === expandedProviderId
  )).length;

  const modelsForProvider = (providerId: string) => draft.models.filter((item) => item.providerId === providerId);

  const updateProvider = (providerId: string, patch: Partial<ModelProvider>) => {
    setDraft((current) => ({
      ...current,
      providers: current.providers.map((item) => (item.id === providerId ? { ...item, ...patch } : item)),
    }));
  };

  const updateModel = (id: string, patch: Partial<ModelEntry>) => {
    setDraft((current) => ({
      ...current,
      models: current.models.map((item) => (item.id === id ? { ...item, ...patch } : item)),
    }));
  };

  const removeModels = (ids: Set<string>) => {
    setDraft((current) => ({
      ...current,
      defaultModelId: ids.has(current.defaultModelId) ? '' : current.defaultModelId,
      models: current.models
        .filter((item) => !ids.has(item.id))
        .map((item) => ({ ...item, fallbackModelIds: item.fallbackModelIds.filter((id) => !ids.has(id)) })),
    }));
  };

  const addProvider = () => {
    const next = emptyProvider();
    setDraft((current) => ({ ...current, providers: [...current.providers, next] }));
    setExpandedProviderId(next.id);
    setExpandedModelId('');
  };

  const removeProvider = (providerId: string) => {
    const modelIds = new Set(draft.models.filter((item) => item.providerId === providerId).map((item) => item.id));
    setDraft((current) => ({
      ...current,
      defaultModelId: modelIds.has(current.defaultModelId) ? '' : current.defaultModelId,
      providers: current.providers.filter((item) => item.id !== providerId),
      models: current.models
        .filter((item) => item.providerId !== providerId)
        .map((item) => ({ ...item, fallbackModelIds: item.fallbackModelIds.filter((id) => !modelIds.has(id)) })),
    }));
    setExpandedProviderId((current) => (current === providerId ? '' : current));
    setExpandedModelId('');
  };

  const addModel = (providerId: string) => {
    const next = emptyModel(providerId);
    setDraft((current) => ({ ...current, models: [...current.models, next] }));
    setExpandedModelId(next.id);
  };

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
          const next: Partial<typeof item> = { ...item };
          delete next.apiKeyConfigured;
          return next;
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

  return (
    <div className="flex h-full min-h-0 flex-col">
      <ModelsToolbar
        loading={loading}
        dirty={dirty}
        saving={saving}
        modelsPath={initial.modelsPath}
        onReload={() => void onReload()}
        onOpenFile={onOpenFile}
        onSave={() => void save()}
      />
      <div className="min-h-0 flex-1 overflow-auto px-8 py-6">
        <fieldset disabled={saving} className="mx-auto max-w-5xl min-w-0 space-y-5">
          <p className="text-sm leading-6 text-[var(--color-text-secondary)]">
            配置供应商连接和可选模型。Chat 只从这里选模型，不在 Agent 里改。
          </p>
          {loadError ? <p role="alert" className="text-sm text-[var(--color-error)]">{loadError}</p> : null}
          <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-4">
        <SelectField
          label="新会话默认模型"
          aria-label="新会话默认模型"
          style={{ width: '100%' }}
          value={draft.defaultModelId || undefined}
          showSearch
          optionFilterProp="label"
          placeholder="选择默认模型"
          onChange={(value) => setDraft({ ...draft, defaultModelId: String(value) })}
          options={availableModels.map((item) => ({
            value: item.id,
            label: `${item.label} · ${draft.providers.find((entry) => entry.id === item.providerId)?.name || '供应商'}`,
          }))}
        />
        <div className="mt-3 flex flex-wrap gap-2 text-xs text-[var(--color-text-tertiary)]">
          <span>{visibleProviders.length} 个已连接供应商</span>
          <span>·</span>
          <span>{availableModels.length} 个可用模型</span>
          {defaultModel ? (
            <>
              <span>·</span>
              <span>当前默认 {defaultModel.label || defaultModel.model}</span>
            </>
          ) : (
            <>
              <span>·</span>
              <span>尚未选择默认模型</span>
            </>
          )}
        </div>
      </div>

      <div className="flex items-center justify-between gap-3">
        <div className="text-sm font-medium text-[var(--color-text-primary)]">供应商</div>
        <Button size="small" icon={<Plus size={14} />} onClick={addProvider}>添加供应商</Button>
      </div>

      {draft.providers.length === 0 ? (
        <p className="rounded-xl border border-dashed border-[var(--color-border)] px-4 py-8 text-center text-sm text-[var(--color-text-tertiary)]">
          还没有供应商。添加一个连接后，再给它配置模型。
        </p>
      ) : (
        <div className="space-y-3">
          {visibleProviders.map((provider) => (
            <ModelProviderEditor
              key={provider.id}
              provider={provider}
              models={modelsForProvider(provider.id)}
              draft={draft}
              open={provider.id === expandedProviderId}
              expandedModelId={expandedModelId}
              isDefaultProvider={modelsForProvider(provider.id).some((item) => item.id === draft.defaultModelId)}
              onToggle={() => {
                setExpandedProviderId(provider.id === expandedProviderId ? '' : provider.id);
                setExpandedModelId('');
              }}
              onChange={(patch) => updateProvider(provider.id, patch)}
              onRemove={() => removeProvider(provider.id)}
              onAddModel={() => addModel(provider.id)}
              onToggleModel={(id) => setExpandedModelId((current) => (current === id ? '' : id))}
              onChangeModel={updateModel}
              onRemoveModel={(id) => removeModels(new Set([id]))}
            />
          ))}
          {hiddenProviderCount > 0 ? (
            <button
              type="button"
              className="w-full rounded-xl border border-dashed border-[var(--color-border)] px-3 py-2.5 text-sm text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]"
              onClick={() => setShowAllProviders((current) => !current)}
            >
              {showAllProviders ? '只看已连接供应商' : `显示其余 ${hiddenProviderCount} 个未连接供应商`}
            </button>
          ) : null}
        </div>
      )}

      {error ? <p role="alert" className="text-sm text-[var(--color-error)]">{error}</p> : null}
        </fieldset>
      </div>
    </div>
  );
}
