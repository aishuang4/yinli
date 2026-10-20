/** Chat / Sandbox / 群聊共用的轻量模型选择器。 */

import { useMemo } from 'react';
import { Settings } from 'lucide-react';
import { useAppStore } from '../../stores/appStore';
import { ComposerPicker } from './ComposerPicker';
import { mergeSelectableModels } from './platformModels';
import type { ModelCatalog } from './useModelCatalog';
import { listSelectableModels, type SelectableModel } from './selectableModels';
export type { SelectableModel } from './selectableModels';

const ADAPTER_MARK: Record<string, string> = {
  openai: 'O',
  deepseek: 'D',
  dashscope: 'Q',
  moonshot: 'K',
  gemini: 'G',
  anthropic: 'A',
  ollama: 'L',
};

export function ModelSelector({
  catalog,
  value,
  onChange,
  disabled,
  loading,
  error,
  platformModels = [],
  platformLoggedIn = false,
  platformOnly = false,
}: {
  catalog: ModelCatalog | null;
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  loading?: boolean;
  error?: string;
  platformModels?: SelectableModel[];
  platformLoggedIn?: boolean;
  platformOnly?: boolean;
}) {
  const setView = useAppStore((state) => state.setView);
  const models = useMemo(
    () => mergeSelectableModels(listSelectableModels(catalog), platformModels, platformLoggedIn, platformOnly),
    [catalog, platformModels, platformLoggedIn, platformOnly],
  );
  const selected = models.find((model) => model.id === value) ?? models[0];
  const triggerLabel = error
    ? '模型配置异常'
    : loading
      ? '加载中…'
      : selected?.label || '选择模型';

  return (
    <ComposerPicker
      ariaLabel="选择模型"
      value={selected?.id || value}
      label={triggerLabel}
      disabled={disabled || loading}
      error={error}
      emptyText="还没有可用模型"
      options={models.map((model) => ({
        id: model.id,
        label: model.label,
        leading: <AdapterMark adapter={model.adapter} name={model.providerName} />,
      }))}
      onChange={onChange}
      footer={platformOnly ? undefined : {
        label: '添加模型',
        icon: <Settings size={14} />,
        onClick: () => setView('models'),
      }}
    />
  );
}

function AdapterMark({ adapter, name }: { adapter: string; name: string }) {
  const mark = ADAPTER_MARK[adapter] || name.trim().slice(0, 1).toUpperCase() || '?';
  return (
    <span
      className="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-[var(--color-bg-hover)] text-[10px] font-medium text-[var(--color-text-secondary)]"
      aria-hidden="true"
    >
      {mark}
    </span>
  );
}
