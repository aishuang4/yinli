/** 单个供应商的连接表单与模型列表。 */

import { useState } from 'react';
import { ChevronDown, ChevronRight, Plus, Trash2 } from 'lucide-react';
import { Button, Checkbox, Tag } from '@/components/design';
import { FormGrid, NumberField, PasswordField, SelectField, TextField } from '../../components/form';
import {
  ADAPTER_OPTIONS,
  adapterLabel,
  providerConnectionLabel,
  providerConnectionStatus,
} from './modelCatalogUi';
import { UpstreamModelImport } from './UpstreamModelImport';
import type { ModelCatalog, ModelEntry, ModelProvider } from './useModelCatalog';

const iconClass = 'inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)] disabled:opacity-40';

export function ModelProviderEditor({
  provider,
  models,
  draft,
  open,
  expandedModelId,
  isDefaultProvider,
  onToggle,
  onChange,
  onRemove,
  onAddModel,
  onImportUpstream,
  onToggleModel,
  onChangeModel,
  onRemoveModel,
}: {
  provider: ModelProvider;
  models: ModelEntry[];
  draft: ModelCatalog;
  open: boolean;
  expandedModelId: string;
  isDefaultProvider: boolean;
  onToggle: () => void;
  onChange: (patch: Partial<ModelProvider>) => void;
  onRemove: () => void;
  onAddModel: () => void;
  onImportUpstream: (names: string[]) => void;
  onToggleModel: (id: string) => void;
  onChangeModel: (id: string, patch: Partial<ModelEntry>) => void;
  onRemoveModel: (id: string) => void;
}) {
  const status = providerConnectionStatus(provider);
  const statusColor = status === 'ready' ? 'green' : status === 'disabled' ? 'transparent' : 'orange';

  return (
    <div className="overflow-hidden rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-secondary)]">
      <button
        type="button"
        className="flex w-full items-center gap-3 px-4 py-3 text-left"
        onClick={onToggle}
        aria-expanded={open}
      >
        {open ? <ChevronDown size={16} className="shrink-0 text-[var(--color-text-tertiary)]" /> : <ChevronRight size={16} className="shrink-0 text-[var(--color-text-tertiary)]" />}
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-medium text-[var(--color-text-primary)]">
            {provider.name.trim() || '未命名供应商'}
          </span>
          <span className="mt-0.5 block truncate text-xs text-[var(--color-text-tertiary)]">
            {adapterLabel(provider.adapter)} · {models.length} 个模型
            {isDefaultProvider ? ' · 含默认模型' : ''}
          </span>
        </span>
        {provider.enabled && status !== 'ready' ? (
          <Tag size="small" color={statusColor} style={{ marginInlineEnd: 0 }}>
            {providerConnectionLabel(status)}
          </Tag>
        ) : null}
        <StatusChip
          checked={provider.enabled}
          onLabel="使用中"
          offLabel="已停用"
          onChange={(enabled) => onChange({ enabled })}
        />
      </button>

      {open ? (
        <div className="space-y-5 border-t border-[var(--color-border)] bg-[var(--color-bg-primary)] px-4 py-4">
          <div>
            <div className="mb-3 text-xs font-medium text-[var(--color-text-tertiary)]">连接</div>
            <FormGrid columns={2}>
              <TextField label="显示名称" aria-label="供应商名称" value={provider.name} onChange={(event) => onChange({ name: event.target.value })} />
              <SelectField
                label="协议"
                aria-label="供应商协议"
                style={{ width: '100%' }}
                value={provider.adapter}
                onChange={(adapter) => onChange({ adapter: String(adapter), requiresApiKey: String(adapter) !== 'ollama' })}
                options={ADAPTER_OPTIONS.map((item) => ({ value: item.value, label: item.label }))}
              />
              <TextField className="col-span-2" label="Base URL" aria-label="Base URL" value={provider.baseUrl} onChange={(event) => onChange({ baseUrl: event.target.value })} />
              <PasswordField
                className="col-span-2"
                label="API Key"
                aria-label="API Key"
                autoComplete="new-password"
                value={provider.apiKey ?? ''}
                placeholder={provider.apiKeyConfigured ? '已配置，留空保持不变' : '未配置'}
                onChange={(event) => onChange({ apiKey: event.target.value })}
              />
            </FormGrid>
            <label className="mt-3 flex cursor-pointer items-start gap-2 text-sm text-[var(--color-text-secondary)]">
              <Checkbox
                checked={provider.requiresApiKey}
                onChange={(event) => onChange({ requiresApiKey: event.target.checked })}
              />
              <span>
                调用时需要 API Key
                <span className="mt-0.5 block text-xs text-[var(--color-text-tertiary)]">Ollama 等本地服务通常不需要。</span>
              </span>
            </label>
            <div className="mt-3 flex flex-wrap items-center justify-end gap-3">
              <button type="button" className="text-xs text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]" onClick={() => onChange({ apiKey: '', apiKeyConfigured: false })}>
                清除 Key
              </button>
              <button type="button" className="text-xs text-[var(--color-error)]" onClick={onRemove}>
                删除供应商
              </button>
            </div>
          </div>

          <div>
            <div className="mb-3 flex items-center justify-between gap-3">
              <div className="text-xs font-medium text-[var(--color-text-tertiary)]">模型</div>
              <Button size="small" icon={<Plus size={14} />} onClick={onAddModel}>添加模型</Button>
            </div>
            {models.length === 0 ? (
              <p className="rounded-lg border border-dashed border-[var(--color-border)] px-3 py-6 text-center text-sm text-[var(--color-text-tertiary)]">
                还没有模型。先添加一个显示名称和模型名。
              </p>
            ) : (
              <div className="overflow-hidden rounded-lg border border-[var(--color-border)]">
                {models.map((model) => (
                  <ModelEditor
                    key={model.id}
                    model={model}
                    draft={draft}
                    open={expandedModelId === model.id}
                    isDefault={draft.defaultModelId === model.id}
                    onToggle={() => onToggleModel(model.id)}
                    onChange={(patch) => onChangeModel(model.id, patch)}
                    onDelete={() => onRemoveModel(model.id)}
                  />
                ))}
              </div>
            )}
            <UpstreamModelImport provider={provider} existing={models} onImport={onImportUpstream} />
          </div>
        </div>
      ) : null}
    </div>
  );
}

function ModelEditor({
  model,
  draft,
  open,
  isDefault,
  onToggle,
  onChange,
  onDelete,
}: {
  model: ModelEntry;
  draft: ModelCatalog;
  open: boolean;
  isDefault: boolean;
  onToggle: () => void;
  onChange: (patch: Partial<ModelEntry>) => void;
  onDelete: () => void;
}) {
  const [advancedOpen, setAdvancedOpen] = useState(false);

  return (
    <div className="border-b border-[var(--color-border)] last:border-b-0">
      <div className="flex items-center gap-2 px-3 py-2.5">
        <button type="button" className="flex min-w-0 flex-1 items-center gap-2 text-left" onClick={onToggle} aria-expanded={open}>
          {open ? <ChevronDown size={15} className="shrink-0 text-[var(--color-text-tertiary)]" /> : <ChevronRight size={15} className="shrink-0 text-[var(--color-text-tertiary)]" />}
          <span className="min-w-0 flex-1">
            <span className="block truncate text-sm font-medium text-[var(--color-text-primary)]">
              {model.label.trim() || '未命名模型'}
            </span>
            <span className="block truncate text-xs text-[var(--color-text-tertiary)]">
              {model.model.trim() || '尚未填写模型名'}
            </span>
          </span>
        </button>
        {isDefault ? <Tag size="small" color="blue" style={{ marginInlineEnd: 0 }}>默认</Tag> : null}
        {model.capabilities.images ? (
          <Tag size="small" color="transparent" style={{ marginInlineEnd: 0 }}>图片</Tag>
        ) : null}
        <StatusChip
          checked={model.enabled}
          onLabel="使用中"
          offLabel="已停用"
          onChange={(enabled) => onChange({ enabled })}
        />
        <button type="button" className={iconClass} title="删除模型" aria-label={`删除模型 ${model.label || '未命名'}`} onClick={onDelete}>
          <Trash2 size={15} />
        </button>
      </div>
      {open ? (
        <div className="space-y-3 border-t border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-3 py-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <TextField label="显示名称" aria-label="模型显示名称" value={model.label} onChange={(event) => onChange({ label: event.target.value })} />
            <TextField label="模型名" aria-label="模型名称" value={model.model} onChange={(event) => onChange({ model: event.target.value })} />
          </div>
          <SelectField
            label="降级模型"
            aria-label={`${model.label || '模型'} 降级模型`}
            mode="multiple"
            style={{ width: '100%' }}
            placeholder="主模型不可用时按顺序改用"
            value={model.fallbackModelIds}
            onChange={(ids) => onChange({ fallbackModelIds: Array.isArray(ids) ? ids.map(String) : [] })}
            options={draft.models
              .filter((item) => item.id !== model.id && item.label.trim() && item.model.trim())
              .map((item) => ({
                value: item.id,
                label: `${item.label} · ${draft.providers.find((entry) => entry.id === item.providerId)?.name || '供应商'}`,
              }))}
          />
          <label className="flex cursor-pointer items-center gap-2 text-sm text-[var(--color-text-secondary)]">
            <Checkbox
              checked={model.capabilities.images}
              onChange={(event) => onChange({ capabilities: { images: event.target.checked } })}
            />
            可用于图片理解
          </label>
          <div>
            <button
              type="button"
              className="inline-flex items-center gap-1 text-xs text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)]"
              onClick={() => setAdvancedOpen((current) => !current)}
              aria-expanded={advancedOpen}
            >
              {advancedOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
              高级参数
            </button>
            {advancedOpen ? (
              <div className="mt-3 space-y-3">
                <div className="grid grid-cols-3 gap-3">
                  <NumberField label="温度" min={0} max={2} step={0.1} value={model.parameters.temperature} onChange={(value) => onChange({ parameters: { ...model.parameters, temperature: Number(value) } })} />
                  <NumberField label="Top P" min={0} max={1} step={0.05} value={model.parameters.topP} onChange={(value) => onChange({ parameters: { ...model.parameters, topP: Number(value) } })} />
                  <NumberField label="最大输出 Token" min={1} step={1} value={model.parameters.maxTokens} onChange={(value) => onChange({ parameters: { ...model.parameters, maxTokens: Number(value) } })} />
                </div>
                <NumberField
                  label="上下文窗口 Token"
                  help="0 表示按供应商默认。一般不用改。"
                  min={0}
                  step={1024}
                  value={model.contextSize ?? 0}
                  onChange={(value) => onChange({ contextSize: Math.max(0, Math.floor(Number(value) || 0)) })}
                />
              </div>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function StatusChip({
  checked,
  onLabel,
  offLabel,
  onChange,
}: {
  checked: boolean;
  onLabel: string;
  offLabel: string;
  onChange: (checked: boolean) => void;
}) {
  return (
    <button
      type="button"
      className="shrink-0"
      aria-pressed={checked}
      onClick={(event) => {
        event.stopPropagation();
        onChange(!checked);
      }}
    >
      <Tag size="small" color={checked ? 'green' : 'transparent'} style={{ marginInlineEnd: 0 }}>
        {checked ? onLabel : offLabel}
      </Tag>
    </button>
  );
}
