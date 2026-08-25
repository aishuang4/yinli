import { useCallback, useEffect, useMemo, useState } from 'react';
import { Card } from '@/components/design';
import { ModelSettingsForm, type ModelSettings } from '../features/settings/ModelSettingsForm';
import { NotificationSettingsCard } from '../features/notifications/NotificationSettingsCard';
import { ConversationContextSettingsCard } from '../features/settings/ConversationContextSettingsCard';
import { UserDataSettingsCard } from '../features/settings/UserDataSettingsCard';
import { useSettingsStore } from '../stores/settingsStore';
import type { ThemeName } from '../types/theme';
import { getErrorMessage, notifyError, notifySuccess } from '../utils/feedback';

export function SettingsPage() {
  const theme = useSettingsStore((state) => state.theme);
  const setTheme = useSettingsStore((state) => state.setTheme);
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
  const [userDataPath, setUserDataPath] = useState<string | null>(null);
  const [userDataLoading, setUserDataLoading] = useState(false);
  const [userDataError, setUserDataError] = useState('');
  const [openingUserData, setOpeningUserData] = useState(false);

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

  const loadUserDataPath = useCallback(async () => {
    if (!window.electronAPI) return;
    setUserDataLoading(true);
    setUserDataError('');
    try {
      setUserDataPath(await window.electronAPI.getUserDataPath());
    } catch (error) {
      setUserDataPath(null);
      setUserDataError(getErrorMessage(error, '用户数据目录读取失败'));
    } finally {
      setUserDataLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadUserDataPath();
  }, [loadUserDataPath]);

  const handleOpenUserData = async () => {
    if (!window.electronAPI || !userDataPath || openingUserData) return;
    setOpeningUserData(true);
    try {
      const result = await window.electronAPI.openPath(userDataPath);
      if (!result.ok) {
        notifyError(`用户数据目录打开失败：${result.error || '未知错误'}`);
      }
    } catch (error) {
      notifyError(`用户数据目录打开失败：${getErrorMessage(error, '未知错误')}`);
    } finally {
      setOpeningUserData(false);
    }
  };

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
    <div className="h-full overflow-auto bg-[var(--color-bg-primary)] px-5 py-6 sm:px-8">
      <div className="mx-auto max-w-7xl">
        <header className="mb-6 flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="text-xl font-semibold text-[var(--color-text-primary)]">系统设置</h1>
            <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
              管理默认模型、消息通知与新会话使用的工作上下文。
            </p>
          </div>
          <span className="text-xs text-[var(--color-text-tertiary)]">保存后将在下一轮对话生效</span>
        </header>

        <div className="grid grid-cols-1 items-start gap-6 xl:grid-cols-[minmax(0,1fr)_352px]">
          <section className="min-w-0">
            <ModelSettingsForm
              settings={settings}
              loading={loading}
              saving={saving}
              notice={notice}
              provider={provider}
              selectedProvider={selectedProvider}
              baseUrl={baseUrl}
              apiKey={apiKey}
              apiKeyEditable={Boolean(apiKeyEditable)}
              defaultModel={defaultModel}
              customProviderName={customProviderName}
              customModels={customModels}
              fallbackModels={fallbackModels}
              modelOptions={modelOptions}
              onProviderChange={handleProviderChange}
              onBaseUrlChange={setBaseUrl}
              onApiKeyChange={setApiKey}
              onDefaultModelChange={setDefaultModel}
              onCustomProviderNameChange={setCustomProviderName}
              onAddCustomModel={addCustomModel}
              onUpdateCustomModel={updateCustomModel}
              onRemoveCustomModel={removeCustomModel}
              onAddFallbackModel={addFallbackModel}
              onUpdateFallbackModel={updateFallbackModel}
              onRemoveFallbackModel={removeFallbackModel}
              onRefresh={() => void loadSettings()}
              onSave={(clearApiKey) => void saveSettings(clearApiKey)}
            />
          </section>

          <aside className="space-y-4 xl:sticky xl:top-0">
            <AppearanceSettingsCard theme={theme} onThemeChange={setTheme} />
            <NotificationSettingsCard />
            <ConversationContextSettingsCard />
            {window.electronAPI && (
              <UserDataSettingsCard
                path={userDataPath}
                loading={userDataLoading}
                opening={openingUserData}
                error={userDataError}
                onOpen={() => void handleOpenUserData()}
                onRetry={() => void loadUserDataPath()}
              />
            )}
          </aside>
        </div>
      </div>
    </div>
  );
}

function AppearanceSettingsCard({
  theme,
  onThemeChange,
}: {
  theme: ThemeName;
  onThemeChange: (theme: ThemeName) => void;
}) {
  const options: Array<{ value: ThemeName; label: string; description: string }> = [
    { value: 'light', label: '亮色主体', description: '浅灰侧栏与白色主工作区' },
    { value: 'dark', label: '暗色主体', description: '深色工作台与低亮度边界' },
  ];

  return (
    <Card bodyStyle={{ padding: 0 }}>
      <div className="p-5">
        <div className="text-sm font-medium text-[var(--color-text-primary)]">界面外观</div>
        <p className="mt-2 text-xs leading-5 text-[var(--color-text-tertiary)]">
          为 Chat 与管理页面选择主题。
        </p>
        <div className="mt-4 grid grid-cols-2 gap-2">
          {options.map((option) => {
            const active = theme === option.value;
            return (
              <button
                key={option.value}
                type="button"
                className={`min-h-[76px] rounded-lg border px-3 py-2.5 text-left transition ${
                  active
                    ? 'border-[var(--color-accent)] bg-[var(--color-accent-bg)]'
                    : 'border-[var(--color-border)] bg-[var(--color-bg-secondary)] hover:bg-[var(--color-bg-hover)]'
                }`}
                onClick={() => onThemeChange(option.value)}
                aria-pressed={active}
              >
                <span className="block text-sm font-medium text-[var(--color-text-primary)]">{option.label}</span>
                <span className="mt-1 block text-xs text-[var(--color-text-tertiary)]">{option.description}</span>
              </button>
            );
          })}
        </div>
      </div>
    </Card>
  );
}
