/**
 * ModelSettingsForm — 系统模型设置表单。
 *
 * 抽离 SettingsPage 的供应商、模型、Base URL、API Key 和降级链配置。
 */

import { KeyRound, Plus, RefreshCw, Save, Trash2 } from 'lucide-react';
import { Button, Card, Tag } from '@agentscope-ai/design';
import { FormField, FormGrid, PasswordField, SelectField, TextField } from '../../components/form';

export interface ModelProviderOption {
  id: string;
  name: string;
  defaultBaseUrl: string;
  defaultModel: string;
  models: string[];
  requiresApiKey: boolean;
  adapter: string;
  configured: boolean;
  apiKeyConfigured: boolean;
  apiKeyPreview: string;
  providerName: string;
  baseUrl: string;
  configuredDefaultModel: string;
  fallbackModels: string[];
  customModels: string[];
}

export interface ModelSettings {
  provider: string;
  providerName: string;
  baseUrl: string;
  defaultModel: string;
  fallbackModels: string[];
  customModels: string[];
  modelOptions: string[];
  apiKeyConfigured: boolean;
  apiKeyPreview: string;
  requiresApiKey: boolean;
  adapter: string;
  providers: ModelProviderOption[];
}

interface ModelSettingsFormProps {
  settings: ModelSettings | null;
  loading: boolean;
  saving: boolean;
  notice: string;
  provider: string;
  selectedProvider?: ModelProviderOption;
  baseUrl: string;
  apiKey: string;
  apiKeyEditable: boolean;
  defaultModel: string;
  customProviderName: string;
  customModels: string[];
  fallbackModels: string[];
  modelOptions: string[];
  onProviderChange: (value: string) => void;
  onBaseUrlChange: (value: string) => void;
  onApiKeyChange: (value: string) => void;
  onDefaultModelChange: (value: string) => void;
  onCustomProviderNameChange: (value: string) => void;
  onAddCustomModel: () => void;
  onUpdateCustomModel: (index: number, value: string) => void;
  onRemoveCustomModel: (index: number) => void;
  onAddFallbackModel: () => void;
  onUpdateFallbackModel: (index: number, value: string) => void;
  onRemoveFallbackModel: (index: number) => void;
  onRefresh: () => void;
  onSave: (clearApiKey?: boolean) => void;
}

export function ModelSettingsForm({
  settings,
  loading,
  saving,
  notice,
  provider,
  selectedProvider,
  baseUrl,
  apiKey,
  apiKeyEditable,
  defaultModel,
  customProviderName,
  customModels,
  fallbackModels,
  modelOptions,
  onProviderChange,
  onBaseUrlChange,
  onApiKeyChange,
  onDefaultModelChange,
  onCustomProviderNameChange,
  onAddCustomModel,
  onUpdateCustomModel,
  onRemoveCustomModel,
  onAddFallbackModel,
  onUpdateFallbackModel,
  onRemoveFallbackModel,
  onRefresh,
  onSave,
}: ModelSettingsFormProps) {
  return (
    <Card bodyStyle={{ padding: 0 }}>
      <div className="space-y-5 p-5">
        <FormGrid>
          <SelectField
            label="模型供应商"
            value={provider}
            loading={loading}
            onChange={(value) => onProviderChange(String(value))}
            options={(settings?.providers ?? []).map((item) => ({
              label: `${item.name}${item.configured ? ' · 已配置' : ' · 未配置'}`,
              value: item.id,
            }))}
          />
          <TextField
            label="默认模型"
            value={defaultModel}
            onChange={(event) => onDefaultModelChange(event.target.value)}
            placeholder="输入任意模型名，例如 deepseek-v4-flash"
          />
        </FormGrid>

        {provider === 'custom_openai' ? (
          <TextField
            label="自定义供应商名称"
            value={customProviderName}
            onChange={(event) => onCustomProviderNameChange(event.target.value)}
            placeholder="例如：公司内网模型网关"
          />
        ) : null}

        {modelOptions.length > 0 ? (
          <FormField label="模型快速填充">
            <div className="flex flex-wrap gap-2">
              {modelOptions.map((model) => (
                <button
                  key={model}
                  type="button"
                  className="rounded-md bg-[var(--color-bg-secondary)] px-2 py-1 text-xs text-[var(--color-text-secondary)] transition hover:bg-[var(--color-bg-hover)]"
                  onClick={() => onDefaultModelChange(model)}
                >
                  {model}
                </button>
              ))}
            </div>
          </FormField>
        ) : null}

        <EditableModelList
          title="自定义模型"
          emptyText="如果供应商列表里没有你的模型，可以在这里添加模型 ID，保存后会出现在 Agent 模型选择中。"
          addText="添加自定义模型"
          placeholder="输入模型 ID，例如 qwen2.5-72b-instruct"
          models={customModels}
          onAdd={onAddCustomModel}
          onUpdate={onUpdateCustomModel}
          onRemove={onRemoveCustomModel}
        />

        <TextField
          label="Base URL"
          value={baseUrl}
          onChange={(event) => onBaseUrlChange(event.target.value)}
          placeholder={selectedProvider?.defaultBaseUrl || 'https://api.example.com/v1'}
        />

        <PasswordField
          label={(
            <span className="flex items-center gap-2">
              <KeyRound size={13} />
              API Key
              {selectedProvider?.apiKeyConfigured ? (
                <Tag size="small" color="success">{selectedProvider.apiKeyPreview}</Tag>
              ) : (
                <Tag size="small" color={selectedProvider?.requiresApiKey ? 'warning' : 'success'}>
                  {selectedProvider?.requiresApiKey ? '未配置' : '无需 Key'}
                </Tag>
              )}
            </span>
          )}
          value={apiKey}
          onChange={(event) => onApiKeyChange(event.target.value)}
          placeholder={selectedProvider?.requiresApiKey ? '输入新的 API Key；留空则保持当前 Key' : '本地模型可留空；需要鉴权的网关可填写 Key'}
          disabled={!apiKeyEditable}
        />

        <EditableModelList
          title="降级模型链"
          emptyText="暂未配置降级模型。"
          addText="添加模型"
          placeholder="输入任意降级模型名"
          models={fallbackModels}
          onAdd={onAddFallbackModel}
          onUpdate={onUpdateFallbackModel}
          onRemove={onRemoveFallbackModel}
        />

        {notice ? (
          <div className="rounded-md bg-[var(--color-bg-secondary)] px-3 py-2 text-sm text-[var(--color-text-secondary)]">
            {notice}
          </div>
        ) : null}

        <div className="flex justify-end gap-2 border-t border-[var(--color-border)] pt-4">
          <Button icon={<RefreshCw size={14} />} onClick={onRefresh} disabled={saving}>
            刷新
          </Button>
          <Button danger onClick={() => onSave(true)} disabled={saving || !selectedProvider?.apiKeyConfigured}>
            清除 Key
          </Button>
          <Button type="primary" icon={<Save size={14} />} loading={saving} onClick={() => onSave(false)}>
            保存设置
          </Button>
        </div>
      </div>
    </Card>
  );
}

function EditableModelList({
  title,
  emptyText,
  addText,
  placeholder,
  models,
  onAdd,
  onUpdate,
  onRemove,
}: {
  title: string;
  emptyText: string;
  addText: string;
  placeholder: string;
  models: string[];
  onAdd: () => void;
  onUpdate: (index: number, value: string) => void;
  onRemove: (index: number) => void;
}) {
  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <span className="text-xs text-[var(--color-text-secondary)]">{title}</span>
        <Button size="small" icon={<Plus size={14} />} onClick={onAdd}>
          {addText}
        </Button>
      </div>
      <div className="space-y-2">
        {models.length === 0 ? (
          <div className="rounded-md bg-[var(--color-bg-secondary)] px-3 py-3 text-sm text-[var(--color-text-tertiary)]">
            {emptyText}
          </div>
        ) : models.map((model, index) => (
          <div key={index} className="grid grid-cols-[minmax(0,1fr)_36px] gap-2">
            <TextField
              value={model}
              onChange={(event) => onUpdate(index, event.target.value)}
              placeholder={placeholder}
            />
            <Button icon={<Trash2 size={14} />} onClick={() => onRemove(index)} />
          </div>
        ))}
      </div>
    </div>
  );
}
