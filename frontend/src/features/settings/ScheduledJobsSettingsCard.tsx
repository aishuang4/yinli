/** ScheduledJobsSettingsCard — 系统设置中的定时任务调度开关。 */

import { useCallback, useEffect, useState } from 'react';
import { Button, Card } from '@/components/design';
import { ToggleField } from '../../components/form';
import { getErrorMessage, notifyError, notifySuccess } from '../../utils/feedback';

interface ScheduledJobsSettings {
  enabled: boolean;
  capabilityGate: boolean;
}

export function ScheduledJobsSettingsCard() {
  const [settings, setSettings] = useState<ScheduledJobsSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const loadSettings = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const response = await fetch('/api/settings/scheduled-jobs');
      if (!response.ok) {
        throw new Error(await response.text());
      }
      setSettings(await response.json() as ScheduledJobsSettings);
    } catch (err) {
      const message = getErrorMessage(err, '定时任务设置加载失败');
      setError(message);
      notifyError(message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadSettings();
  }, [loadSettings]);

  const saveSettings = async (patch: Partial<ScheduledJobsSettings>) => {
    if (!settings) return;
    const next = { ...settings, ...patch };
    setSaving(true);
    try {
      const response = await fetch('/api/settings/scheduled-jobs', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(next),
      });
      if (!response.ok) {
        throw new Error(await response.text());
      }
      setSettings(await response.json() as ScheduledJobsSettings);
      notifySuccess(next.enabled ? '定时任务调度已开启。' : '定时任务调度已关闭。');
    } catch (error) {
      notifyError(getErrorMessage(error, '定时任务设置保存失败'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card styles={{ body: { padding: 0 } }}>
      <div className="p-5">
        <div className="text-sm font-medium text-[var(--color-text-primary)]">定时任务调度</div>
        <p className="mt-2 text-xs leading-5 text-[var(--color-text-tertiary)]">
          关闭后对话里无法登记新任务。Windows 安装版首次启动会默认打开。
        </p>
        {error ? (
          <div className="mt-4 flex items-center justify-between gap-2">
            <span className="text-xs text-[var(--color-error)]">{error}</span>
            <Button size="small" onClick={() => void loadSettings()}>重试</Button>
          </div>
        ) : loading || !settings ? (
          <div className="mt-4 text-xs text-[var(--color-text-tertiary)]">正在加载…</div>
        ) : (
          <div className="mt-4 space-y-3">
            <div className="flex items-center justify-between gap-3">
              <div className="min-w-0 text-sm text-[var(--color-text-primary)]">启用调度</div>
              <ToggleField
                checked={settings.enabled}
                disabled={saving}
                onChange={(checked) => void saveSettings({ enabled: checked })}
                checkedLabel="开启"
                uncheckedLabel="关闭"
              />
            </div>
            <div className="flex items-center justify-between gap-3">
              <div className="min-w-0">
                <div className="text-sm text-[var(--color-text-primary)]">能力门禁</div>
                <div className="mt-0.5 text-xs text-[var(--color-text-tertiary)]">
                  到期必须使用 Job Agent，必注入工具不会被门禁裁掉。
                </div>
              </div>
              <ToggleField
                checked={settings.capabilityGate}
                disabled={saving || !settings.enabled}
                onChange={(checked) => void saveSettings({ capabilityGate: checked })}
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
