/** GroupStudioSettingsCard — 系统设置中的群聊工作室开关。默认开启。 */

import { useCallback, useEffect, useState } from 'react';
import { Button, Card } from '@/components/design';
import { ToggleField } from '../../components/form';
import { getErrorMessage, notifyError, notifySuccess } from '../../utils/feedback';

interface GroupStudioSettings {
  enabled: boolean;
  maxMembers: number;
  maxParallelWorkers: number;
  maxTurns: number;
}

export function GroupStudioSettingsCard() {
  const [settings, setSettings] = useState<GroupStudioSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const loadSettings = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const response = await fetch('/api/settings/group-studio');
      if (!response.ok) {
        throw new Error(await response.text());
      }
      setSettings(await response.json() as GroupStudioSettings);
    } catch (err) {
      const message = getErrorMessage(err, '工作室设置加载失败');
      setError(message);
      notifyError(message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadSettings();
  }, [loadSettings]);

  const saveSettings = async (patch: Partial<GroupStudioSettings>) => {
    if (!settings) return;
    const next = { ...settings, ...patch };
    setSaving(true);
    try {
      const response = await fetch('/api/settings/group-studio', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(next),
      });
      if (!response.ok) {
        throw new Error(await response.text());
      }
      setSettings(await response.json() as GroupStudioSettings);
      notifySuccess(next.enabled ? '群聊工作室已开启。' : '群聊工作室已关闭。');
    } catch (saveError) {
      notifyError(getErrorMessage(saveError, '工作室设置保存失败'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card bodyStyle={{ padding: 0 }}>
      <div className="p-5">
        <div className="text-sm font-medium text-[var(--color-text-primary)]">群聊工作室</div>
        <p className="mt-2 text-xs leading-5 text-[var(--color-text-tertiary)]">
          关闭后无法从 Chat 进入工作室，相关 API 返回不可用。默认开启。
        </p>
        {error ? (
          <div className="mt-4 flex items-center justify-between gap-2">
            <span className="text-xs text-[var(--color-error)]">{error}</span>
            <Button size="small" onClick={() => void loadSettings()}>重试</Button>
          </div>
        ) : loading || !settings ? (
          <div className="mt-4 text-xs text-[var(--color-text-tertiary)]">正在加载…</div>
        ) : (
          <div className="mt-4">
            <div className="flex items-center justify-between gap-3">
              <div className="min-w-0 text-sm text-[var(--color-text-primary)]">启用工作室</div>
              <ToggleField
                checked={settings.enabled}
                disabled={saving}
                onChange={(checked) => void saveSettings({ enabled: checked })}
                checkedLabel="开启"
                uncheckedLabel="关闭"
              />
            </div>
          </div>
        )}
      </div>
    </Card>
  );
}
