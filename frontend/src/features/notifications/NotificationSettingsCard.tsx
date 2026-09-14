/** NotificationSettingsCard — 设置页中的通知通道配置。 */

import { useCallback, useEffect, useState } from 'react';
import { Button, Card, Tag } from '@/components/design';
import { TextField, ToggleField } from '../../components/form';
import type { NotificationChannelSettings } from '../../types/notification';
import { getErrorMessage, notifyError, notifySuccess } from '../../utils/feedback';

export function NotificationSettingsCard() {
  const [channels, setChannels] = useState<NotificationChannelSettings[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState('');

  const loadSettings = useCallback(async () => {
    setLoading(true);
    setNotice('');
    try {
      const response = await fetch('/api/notifications/settings');
      if (!response.ok) {
        throw new Error(await response.text());
      }
      const payload = await response.json() as { channels: NotificationChannelSettings[] };
      setChannels(payload.channels);
    } catch (error) {
      setNotice(getErrorMessage(error, '通知设置加载失败'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadSettings();
  }, [loadSettings]);

  const updateChannel = (channelId: string, patch: Partial<NotificationChannelSettings>) => {
    setChannels((current) => current.map((item) => (
      item.id === channelId ? { ...item, ...patch } : item
    )));
  };

  const updateConfig = (channelId: string, key: string, value: string) => {
    setChannels((current) => current.map((item) => (
      item.id === channelId
        ? { ...item, config: { ...item.config, [key]: value } }
        : item
    )));
  };

  const saveSettings = async () => {
    setSaving(true);
    setNotice('');
    try {
      const response = await fetch('/api/notifications/settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          channels: channels.map((item) => ({
            id: item.id,
            enabled: item.enabled,
            config: item.config,
          })),
        }),
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => null) as { detail?: unknown } | null;
        const detail = typeof payload?.detail === 'string' ? payload.detail : '';
        throw new Error(detail || `HTTP ${response.status}`);
      }
      const payload = await response.json() as { channels: NotificationChannelSettings[] };
      setChannels(payload.channels);
      const nextNotice = '通知设置已保存。';
      setNotice(nextNotice);
      notifySuccess(nextNotice);
    } catch (error) {
      const message = getErrorMessage(error, '通知设置保存失败');
      setNotice(message);
      notifyError(`通知设置保存失败：${message}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card styles={{ body: { padding: 0 } }}>
      <div className="p-5">
        <div className="text-sm font-medium text-[var(--color-text-primary)]">消息通知</div>
        <p className="mt-2 text-xs leading-5 text-[var(--color-text-tertiary)]">
          定时任务完成后会推送到 Chat 右上角铃铛。邮箱、微信、飞书通道已预留，后续可直接接入实现。
        </p>
        {loading ? (
          <div className="mt-4 text-xs text-[var(--color-text-tertiary)]">正在加载通知通道…</div>
        ) : (
          <div className="mt-4 space-y-3">
            {channels.map((channel) => (
              <div
                key={channel.id}
                className="rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-3.5"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-[var(--color-text-primary)]">
                        {channel.displayName}
                      </span>
                      {channel.alwaysOn ? (
                        <Tag size="small" color="blue">始终开启</Tag>
                      ) : null}
                      {!channel.implemented ? (
                        <Tag size="small" color="transparent">即将接入</Tag>
                      ) : null}
                    </div>
                    <p className="mt-1 text-xs leading-5 text-[var(--color-text-tertiary)]">
                      {channel.description}
                    </p>
                  </div>
                  <ToggleField
                    checked={channel.enabled}
                    disabled={channel.alwaysOn || !channel.implemented || saving}
                    onChange={(checked) => updateChannel(channel.id, { enabled: checked })}
                    checkedLabel="开启"
                    uncheckedLabel="关闭"
                  />
                </div>
                {channel.configFields.length > 0 ? (
                  <div className="mt-3 space-y-2">
                    {channel.configFields.map((field) => (
                      <TextField
                        key={field.key}
                        label={field.label}
                        value={channel.config[field.key] || ''}
                        placeholder={field.placeholder}
                        disabled={saving}
                        onChange={(event) => updateConfig(channel.id, field.key, event.target.value)}
                      />
                    ))}
                  </div>
                ) : null}
              </div>
            ))}
          </div>
        )}
        {notice ? (
          <div className="mt-3 text-xs text-[var(--color-text-secondary)]">{notice}</div>
        ) : null}
        <Button
          className="mt-4 w-full justify-center"
          size="small"
          loading={saving}
          disabled={loading}
          onClick={() => void saveSettings()}
        >
          保存通知设置
        </Button>
      </div>
    </Card>
  );
}
