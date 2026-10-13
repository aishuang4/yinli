/** 供应商内部的上游模型拉取。默认收起，勾选后才进入待保存目录。 */

import { useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { Button, Checkbox } from '@/components/design';
import type { ModelEntry, ModelProvider } from './useModelCatalog';

export function UpstreamModelImport({
  provider,
  existing,
  onImport,
}: {
  provider: ModelProvider;
  existing: ModelEntry[];
  onImport: (names: string[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [names, setNames] = useState<string[] | null>(null);
  const [picked, setPicked] = useState<string[]>([]);
  const saved = provider.apiKeyConfigured || Boolean(provider.apiKey?.trim());

  const load = async () => {
    setLoading(true);
    setError('');
    try {
      const response = await fetch('/api/settings/models/upstream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ providerId: provider.id }),
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(typeof data.detail === 'string' ? data.detail : '拉取失败');
      }
      const listed = Array.isArray(data.models) ? data.models.map((item: unknown) => String(item)) : [];
      setNames(listed);
      setPicked([]);
    } catch (cause) {
      setNames(null);
      setError(cause instanceof Error ? cause.message : '拉取失败');
    } finally {
      setLoading(false);
    }
  };

  const add = () => {
    const have = new Set(existing.map((item) => item.model));
    onImport(picked.filter((name) => !have.has(name)));
    setPicked([]);
  };

  return (
    <div className="rounded-lg border border-dashed border-[var(--color-border)]">
      <button
        type="button"
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-[var(--color-text-tertiary)]"
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
      >
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        从供应商拉取模型
        <span className="text-[var(--color-text-tertiary)]">先保存连接，再按需加入目录</span>
      </button>
      {open ? (
        <div className="space-y-3 border-t border-[var(--color-border)] px-3 py-3">
          {!saved ? (
            <p className="text-xs text-[var(--color-text-tertiary)]">保存供应商和 API Key 之后才能拉取。拉取结果不会自动变成默认模型。</p>
          ) : (
            <>
              <div className="flex items-center justify-between gap-3">
                <p className="text-xs text-[var(--color-text-tertiary)]">只加入勾选的模型。保存配置后才会写入 models.json。</p>
                <Button size="small" loading={loading} onClick={() => void load()}>拉取列表</Button>
              </div>
              {error ? <p className="text-xs text-[var(--color-error)]">{error}</p> : null}
              {names && names.length === 0 ? <p className="text-xs text-[var(--color-text-tertiary)]">上游没有返回模型。</p> : null}
              {names && names.length > 0 ? (
                <div className="max-h-48 space-y-1 overflow-auto">
                  {names.map((name) => {
                    const already = existing.some((item) => item.model === name);
                    return (
                      <label key={name} className="flex items-center gap-2 text-sm text-[var(--color-text-primary)]">
                        <Checkbox
                          checked={already || picked.includes(name)}
                          disabled={already}
                          onChange={(event) => {
                            setPicked((current) => (
                              event.target.checked
                                ? [...current, name]
                                : current.filter((item) => item !== name)
                            ));
                          }}
                        />
                        <span className="truncate">{name}{already ? ' · 已在目录' : ''}</span>
                      </label>
                    );
                  })}
                </div>
              ) : null}
              <div className="flex justify-end">
                <Button size="small" disabled={picked.length === 0} onClick={add}>加入目录</Button>
              </div>
            </>
          )}
        </div>
      ) : null}
    </div>
  );
}
