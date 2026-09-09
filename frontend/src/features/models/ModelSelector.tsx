/** Chat 与 Sandbox 共用的供应商分组模型选择器。 */
import { SelectField } from '../../components/form';
import { Settings } from 'lucide-react';
import { useAppStore } from '../../stores/appStore';
import { modelAvailable, type ModelCatalog } from './useModelCatalog';

export function ModelSelector({ catalog, value, onChange, disabled, loading, error }: {
  catalog: ModelCatalog | null; value: string; onChange: (value: string) => void;
  disabled?: boolean; loading?: boolean; error?: string;
}) {
  const setView = useAppStore(state => state.setView);
  const options = catalog?.providers.map(provider => ({
    label: provider.name,
    options: catalog.models.filter(model => model.providerId === provider.id && modelAvailable(catalog, model)).map(model => ({
      value: model.id, label: `${model.label} · ${provider.name}`,
    })),
  })).filter(group => group.options.length) ?? [];
  return <SelectField aria-label="选择模型" title={error || undefined}
    value={value || undefined} onChange={value => onChange(String(value))} options={options}
    placeholder={error ? '模型配置异常' : '选择模型'} showSearch optionFilterProp="label"
    disabled={disabled} loading={loading} status={error ? 'error' : undefined}
    className="w-[210px] max-w-full shrink"
    popupMatchSelectWidth={340}
    popupRender={menu => <>{menu}<button type="button"
      className="flex w-full items-center gap-2 border-t border-[var(--color-border)] px-3 py-2 text-sm"
      onClick={() => setView('settings')}><Settings size={14} />模型管理</button></>}
  />;
}
