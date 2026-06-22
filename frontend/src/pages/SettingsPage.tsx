import { useCallback, useEffect, useMemo, useState } from 'react';
import { Button, Card, Input, Select, Tag } from '@agentscope-ai/design';
import { KeyRound, Plus, RefreshCw, Save, Trash2 } from 'lucide-react';
import { getErrorMessage, notifyError, notifySuccess } from '../utils/feedback';

interface ModelProviderOption {
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

interface ModelSettings {
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

export function SettingsPage() {
  const [settings, setSettings] = useState<ModelSettings | null>(null);
  const [provider, setProvider] = useState('deepseek');
  const [baseUrl, setBaseUrl] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [defaultModel, setDefaultModel] = useState('');
  const [fallbackModels, setFallbackModels] = useState<string[]>([]);
  const [customModels, setCustomModels] = useState<string[]>([]);
  const [customProviderName, setCustomProviderName] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState('');

  const selectedProvider = settings?.providers.find((item) => item.id === provider);
  const apiKeyEditable = selectedProvider?.requiresApiKey || selectedProvider?.id === 'custom_openai';
  const modelOptions = useMemo(() => {
    const providerModels = selectedProvider?.models ?? [];
    return Array.from(new Set([
      ...providerModels,
      defaultModel,
      ...fallbackModels,
      ...customModels,
    ].map((item) => item.trim()).filter(Boolean)));
  }, [customModels, defaultModel, fallbackModels, selectedProvider]);

  const applySettings = useCallback((data: ModelSettings) => {
    setSettings(data);
    setProvider(data.provider);
    setBaseUrl(data.baseUrl);
    setDefaultModel(data.defaultModel);
    setFallbackModels(data.fallbackModels.length ? data.fallbackModels : []);
    setCustomModels(data.customModels.length ? data.customModels : []);
    setCustomProviderName(data.providerName);
    setApiKey('');
  }, []);

  const loadSettings = useCallback(async () => {
    setLoading(true);
    setNotice('');
    try {
      const response = await fetch('/api/settings/model');
      if (!response.ok) {
        throw new Error(await response.text());
      }
      applySettings(await response.json());
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error));
    } finally {
      setLoading(false);
    }
  }, [applySettings]);

  useEffect(() => {
    void loadSettings();
  }, [loadSettings]);

  const handleProviderChange = (value: string) => {
    const nextProvider = settings?.providers.find((item) => item.id === value);
    setProvider(value);
    if (nextProvider) {
      setBaseUrl(nextProvider.baseUrl || nextProvider.defaultBaseUrl);
      setDefaultModel(nextProvider.configuredDefaultModel || nextProvider.defaultModel);
      setFallbackModels(nextProvider.fallbackModels || []);
      setCustomModels(nextProvider.customModels || []);
      setCustomProviderName(nextProvider.providerName || nextProvider.name);
      setApiKey('');
    }
  };

  const saveSettings = async (clearApiKey = false) => {
    setSaving(true);
    setNotice('');
    try {
      const response = await fetch('/api/settings/model', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          provider,
          providerName: customProviderName,
          baseUrl,
          defaultModel,
          fallbackModels,
          customModels,
          apiKey: apiKey || undefined,
          clearApiKey,
        }),
      });
      if (!response.ok) {
        throw new Error(await response.text());
      }
      applySettings(await response.json());
      const nextNotice = clearApiKey ? 'API Key 已清除。' : '模型设置已保存，下一轮对话生效。';
      setNotice(nextNotice);
      notifySuccess(nextNotice);
    } catch (error) {
      const message = getErrorMessage(error, '模型设置保存失败');
      setNotice(message);
      notifyError(`模型设置保存失败：${message}`);
    } finally {
      setSaving(false);
    }
  };

  const addFallbackModel = () => {
    setFallbackModels((current) => [...current, '']);
  };

  const updateFallbackModel = (index: number, value: string) => {
    setFallbackModels((current) => current.map((item, itemIndex) => (itemIndex === index ? value : item)));
  };

  const removeFallbackModel = (index: number) => {
    setFallbackModels((current) => current.filter((_, itemIndex) => itemIndex !== index));
  };

  const addCustomModel = () => {
    setCustomModels((current) => [...current, '']);
  };

  const updateCustomModel = (index: number, value: string) => {
    setCustomModels((current) => current.map((item, itemIndex) => (itemIndex === index ? value : item)));
  };

  const removeCustomModel = (index: number) => {
    setCustomModels((current) => current.filter((_, itemIndex) => itemIndex !== index));
  };

  return (
    <div className="h-full overflow-auto bg-[var(--color-bg-primary)] px-8 py-6">
      <div className="mx-auto grid max-w-6xl grid-cols-[minmax(0,1fr)_320px] gap-6">
        <section className="min-w-0 space-y-4">
          <div>
            <h1 className="text-xl font-semibold text-[var(--color-text-primary)]">系统设置</h1>
            <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
              配置默认模型供应商、API Key 和全局降级模型链。
            </p>
          </div>

          <Card bodyStyle={{ padding: 0 }}>
            <div className="space-y-5 p-5">
              <div className="grid grid-cols-2 gap-4">
                <label className="block">
                  <span className="mb-1 block text-xs text-[var(--color-text-secondary)]">模型供应商</span>
	                  <Select
                    value={provider}
                    loading={loading}
                    onChange={handleProviderChange}
                    options={(settings?.providers ?? []).map((item) => ({
                      label: `${item.name}${item.configured ? ' · 已配置' : ' · 未配置'}`,
                      value: item.id,
                    }))}
	                    style={{ width: '100%' }}
	                  />
	                </label>

	                <label className="block">
	                  <span className="mb-1 block text-xs text-[var(--color-text-secondary)]">默认模型</span>
	                  <Input
	                    value={defaultModel}
	                    onChange={(event) => setDefaultModel(event.target.value)}
	                    placeholder="输入任意模型名，例如 deepseek-chat"
	                  />
	                </label>
	              </div>

              {provider === 'custom_openai' ? (
                <label className="block">
                  <span className="mb-1 block text-xs text-[var(--color-text-secondary)]">自定义供应商名称</span>
                  <Input
                    value={customProviderName}
                    onChange={(event) => setCustomProviderName(event.target.value)}
                    placeholder="例如：公司内网模型网关"
                  />
                </label>
              ) : null}

              {modelOptions.length > 0 ? (
                <div>
                  <span className="mb-2 block text-xs text-[var(--color-text-secondary)]">模型快速填充</span>
                  <div className="flex flex-wrap gap-2">
                    {modelOptions.map((model) => (
                      <button
                        key={model}
                        type="button"
                        className="rounded-md bg-[var(--color-bg-secondary)] px-2 py-1 text-xs text-[var(--color-text-secondary)] transition hover:bg-[var(--color-bg-hover)]"
                        onClick={() => setDefaultModel(model)}
                      >
                        {model}
                      </button>
                    ))}
                  </div>
                </div>
              ) : null}

              <div>
                <div className="mb-2 flex items-center justify-between">
                  <span className="text-xs text-[var(--color-text-secondary)]">自定义模型</span>
                  <Button size="small" icon={<Plus size={14} />} onClick={addCustomModel}>
                    添加自定义模型
                  </Button>
                </div>
                <div className="space-y-2">
                  {customModels.length === 0 ? (
                    <div className="rounded-md bg-[var(--color-bg-secondary)] px-3 py-3 text-sm text-[var(--color-text-tertiary)]">
                      如果供应商列表里没有你的模型，可以在这里添加模型 ID，保存后会出现在 Agent 模型选择中。
                    </div>
                  ) : customModels.map((model, index) => (
                    <div key={index} className="grid grid-cols-[minmax(0,1fr)_36px] gap-2">
                      <Input
                        value={model}
                        onChange={(event) => updateCustomModel(index, event.target.value)}
                        placeholder="输入模型 ID，例如 qwen2.5-72b-instruct"
                      />
                      <Button icon={<Trash2 size={14} />} onClick={() => removeCustomModel(index)} />
                    </div>
                  ))}
                </div>
              </div>

              <label className="block">
                <span className="mb-1 block text-xs text-[var(--color-text-secondary)]">Base URL</span>
                <Input
                  value={baseUrl}
                  onChange={(event) => setBaseUrl(event.target.value)}
                  placeholder={selectedProvider?.defaultBaseUrl || 'https://api.example.com/v1'}
                />
              </label>

              <label className="block">
                <span className="mb-1 flex items-center gap-2 text-xs text-[var(--color-text-secondary)]">
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
                <Input.Password
                  value={apiKey}
                  onChange={(event) => setApiKey(event.target.value)}
                  placeholder={selectedProvider?.requiresApiKey ? '输入新的 API Key；留空则保持当前 Key' : '本地模型可留空；需要鉴权的网关可填写 Key'}
                  disabled={!apiKeyEditable}
                />
              </label>

              <div>
                <div className="mb-2 flex items-center justify-between">
                  <span className="text-xs text-[var(--color-text-secondary)]">降级模型链</span>
                  <Button size="small" icon={<Plus size={14} />} onClick={addFallbackModel}>
                    添加模型
                  </Button>
                </div>
                <div className="space-y-2">
                  {fallbackModels.length === 0 ? (
                    <div className="rounded-md bg-[var(--color-bg-secondary)] px-3 py-3 text-sm text-[var(--color-text-tertiary)]">
                      暂未配置降级模型。
                    </div>
                  ) : fallbackModels.map((model, index) => (
	                    <div key={index} className="grid grid-cols-[minmax(0,1fr)_36px] gap-2">
	                      <Input
	                        value={model}
	                        onChange={(event) => updateFallbackModel(index, event.target.value)}
	                        placeholder="输入任意降级模型名"
	                      />
                      <Button icon={<Trash2 size={14} />} onClick={() => removeFallbackModel(index)} />
                    </div>
                  ))}
                </div>
              </div>

              {notice ? (
                <div className="rounded-md bg-[var(--color-bg-secondary)] px-3 py-2 text-sm text-[var(--color-text-secondary)]">
                  {notice}
                </div>
              ) : null}

              <div className="flex justify-end gap-2 border-t border-[var(--color-border)] pt-4">
                <Button icon={<RefreshCw size={14} />} onClick={loadSettings} disabled={saving}>
                  刷新
                </Button>
                <Button danger onClick={() => void saveSettings(true)} disabled={saving || !selectedProvider?.apiKeyConfigured}>
                  清除 Key
                </Button>
                <Button type="primary" icon={<Save size={14} />} loading={saving} onClick={() => void saveSettings(false)}>
                  保存设置
                </Button>
              </div>
            </div>
          </Card>
        </section>

        <aside className="space-y-4">
          <Card bodyStyle={{ padding: 0 }}>
            <div className="p-4">
              <div className="text-sm font-medium text-[var(--color-text-primary)]">当前运行配置</div>
              <dl className="mt-4 space-y-3 text-sm">
	                <div>
	                  <dt className="text-xs text-[var(--color-text-tertiary)]">供应商</dt>
	                  <dd className="mt-1 text-[var(--color-text-primary)]">{settings?.providerName || '-'}</dd>
	                </div>
                <div>
                  <dt className="text-xs text-[var(--color-text-tertiary)]">适配器</dt>
                  <dd className="mt-1 text-[var(--color-text-primary)]">{settings?.adapter || '-'}</dd>
                </div>
                <div>
                  <dt className="text-xs text-[var(--color-text-tertiary)]">默认模型</dt>
                  <dd className="mt-1 text-[var(--color-text-primary)]">{settings?.defaultModel || '-'}</dd>
                </div>
                <div>
                  <dt className="text-xs text-[var(--color-text-tertiary)]">Key 状态</dt>
                  <dd className="mt-1">
                    <Tag size="small" color={settings?.apiKeyConfigured ? 'success' : 'warning'}>
                      {settings?.apiKeyConfigured ? '已配置' : '未配置'}
                    </Tag>
                  </dd>
                </div>
              </dl>
            </div>
          </Card>

          <Card bodyStyle={{ padding: 0 }}>
            <div className="p-4 text-sm text-[var(--color-text-secondary)]">
              <div className="font-medium text-[var(--color-text-primary)]">生效规则</div>
              <p className="mt-3 leading-6">
                系统设置作为全局默认值。单个 Agent 如果配置了自己的主模型，会优先使用 Agent 的模型名，但供应商、Base URL 和 API Key 仍来自这里。
              </p>
            </div>
          </Card>
        </aside>
      </div>
    </div>
  );
}
