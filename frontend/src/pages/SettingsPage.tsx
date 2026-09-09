import { useCallback, useEffect, useState } from 'react';
import { Card } from '@/components/design';
import { ModelsManager } from '../features/models/ModelsManager';
import { NotificationSettingsCard } from '../features/notifications/NotificationSettingsCard';
import { GroupStudioSettingsCard } from '../features/settings/GroupStudioSettingsCard';
import { ScheduledJobsSettingsCard } from '../features/settings/ScheduledJobsSettingsCard';
import { ConversationContextSettingsCard } from '../features/settings/ConversationContextSettingsCard';
import { UserDataSettingsCard } from '../features/settings/UserDataSettingsCard';
import { useSettingsStore } from '../stores/settingsStore';
import type { ThemeName } from '../types/theme';
import { getErrorMessage, notifyError } from '../utils/feedback';

export function SettingsPage() {
  const theme = useSettingsStore((state) => state.theme);
  const setTheme = useSettingsStore((state) => state.setTheme);
  const [userDataPath, setUserDataPath] = useState<string | null>(null);
  const [userDataLoading, setUserDataLoading] = useState(false);
  const [userDataError, setUserDataError] = useState('');
  const [openingUserData, setOpeningUserData] = useState(false);

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

  return (
    <div className="h-full overflow-auto bg-[var(--color-bg-primary)] px-5 py-6 sm:px-8">
      <div className="mx-auto max-w-3xl">
        <header className="mb-6 flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="text-xl font-semibold text-[var(--color-text-primary)]">系统设置</h1>
            <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
              管理定时任务调度、默认模型、消息通知与新会话使用的工作上下文。
            </p>
          </div>
          <span className="text-xs text-[var(--color-text-tertiary)]">保存后将在下一轮对话生效</span>
        </header>

        <div className="flex flex-col gap-6">
          <ScheduledJobsSettingsCard />
          <GroupStudioSettingsCard />
          <AppearanceSettingsCard theme={theme} onThemeChange={setTheme} />
          <ModelsManager />
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
