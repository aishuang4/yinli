/** 自动审核只引用当前模型目录；删除/停用保留失效提示，避免静默换模型。 */
import { SelectField } from '../../components/form';
import { reviewModelAvailable, type ModelCatalog } from './useModelCatalog';
import { usePermissionReviewStatus } from './usePermissionReviewStatus';

export function PermissionReviewModelField({ catalog, onChange }: {
  catalog: ModelCatalog;
  onChange: (value: ModelCatalog['permissionReview']) => void;
}) {
  const { status } = usePermissionReviewStatus();
  const selectedId = catalog.permissionReview?.modelConfigId;
  const selected = catalog.models.find(item => item.id === selectedId);
  const unavailable = Boolean(selectedId && (!selected || !reviewModelAvailable(catalog, selected)));
  const options = catalog.models.filter(item => reviewModelAvailable(catalog, item)).map(item => ({
    value: item.id,
    label: `${item.label} · ${catalog.providers.find(provider => provider.id === item.providerId)?.name || '供应商'}`,
  }));
  if (unavailable && selectedId) options.push({ value: selectedId, label: `${selected?.label || selectedId}（不可用）` });

  return (
    <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-4">
      <SelectField
        label="自动审核模型"
        aria-label="自动审核模型"
        help="用于 Chat 的智能审核。非只读操作交给这个模型判断，无法判断时转人工。"
        style={{ width: '100%' }}
        value={selectedId || undefined}
        allowClear
        showSearch
        optionFilterProp="label"
        placeholder="尚未配置"
        options={options}
        onChange={(value) => onChange(value ? {
          ...(catalog.permissionReview || { timeoutSeconds: 8, maxOutputTokens: 512, schemaVersion: 1, promptVersion: 'permission-review-v1' }),
          modelConfigId: String(value),
        } : null)}
      />
      {unavailable ? <p role="alert" className="mt-2 text-sm text-[var(--color-error)]">自动审核模型不可用，请重新选择或启用连接。</p> : null}
      {selectedId && !unavailable ? <p className="mt-2 text-sm text-[var(--color-text-secondary)]">{status.reason}</p> : null}
    </div>
  );
}
