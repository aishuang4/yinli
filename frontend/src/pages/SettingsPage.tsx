import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Card } from '@/components/design';
import { ModelsManager } from '../features/models/ModelsManager';
import { NotificationSettingsCard } from '../features/notifications/NotificationSettingsCard';
import { GroupStudioSettingsCard } from '../features/settings/GroupStudioSettingsCard';
import { ScheduledJobsSettingsCard } from '../features/settings/ScheduledJobsSettingsCard';
import { ConversationContextSettingsCard } from '../features/settings/ConversationContextSettingsCard';
import { UserDataSettingsCard } from '../features/settings/UserDataSettingsCard';
import { SettingsToc } from '../features/settings/SettingsToc';
import { visibleSettingsNavItems } from '../features/settings/settingsNav';
import { useSettingsStore } from '../stores/settingsStore';
import { isSurfaceEnabled, isViewEnabled } from '../stores/productFeatures';
import { useProductFeaturesStore } from '../stores/productFeaturesStore';
import type { ThemeName } from '../types/theme';
import { getErrorMessage, notifyError } from '../utils/feedback';

export function SettingsPage() {
  const theme = useSettingsStore((state) => state.theme);
  const features = useProductFeaturesStore((state) => state.features);
  const setTheme = useSettingsStore((state) => state.setTheme);
  const [userDataPath, setUserDataPath] = useState<string | null>(null);
  const [userDataLoading, setUserDataLoading] = useState(false);
  const [userDataError, setUserDataError] = useState('');
  const [openingUserData, setOpeningUserData] = useState(false);
  const [activeSection, setActiveSection] = useState('settings-appearance');
  const scrollerRef = useRef<HTMLDivElement>(null);
  const navItems = useMemo(() => visibleSettingsNavItems({
    jobs: isViewEnabled(features, 'jobs'),
    groupStudio: isSurfaceEnabled(features, 'groupStudio'),
    userData: Boolean(window.electronAPI),
  }), [features]);

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

  useEffect(() => {
    if (!navItems.some((item) => item.id === activeSection)) {
      setActiveSection(navItems[0]?.id || '');
    }
  }, [activeSection, navItems]);

  useEffect(() => {
    const root = scrollerRef.current;
    if (!root || navItems.length === 0) return undefined;
    const ratios = new Map<string, number>();
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.target.id) ratios.set(entry.target.id, entry.intersectionRatio);
      });
      const next = navItems
        .slice()
        .reverse()
        .find((item) => (ratios.get(item.id) || 0) > 0.12);
      if (next) setActiveSection(next.id);
    }, { root, rootMargin: '-12% 0px -64% 0px', threshold: [0, 0.12, 0.3, 0.6] });
    navItems.forEach((item) => {
      const node = root.querySelector(`#${item.id}`);
      if (node) observer.observe(node);
    });
    return () => observer.disconnect();
  }, [navItems]);

  const jumpToSection = (id: string) => {
    const target = document.getElementById(id);
    if (!target) return;
    setActiveSection(id);
    let scroller: HTMLElement | null = scrollerRef.current;
    if (!scroller || scroller.scrollHeight <= scroller.clientHeight + 2) {
      scroller = target.parentElement;
      while (scroller && scroller.scrollHeight <= scroller.clientHeight + 2) {
        scroller = scroller.parentElement;
      }
    }
    if (!scroller) return;
    const top = target.getBoundingClientRect().top - scroller.getBoundingClientRect().top + scroller.scrollTop - 16;
    scroller.scrollTo({ top: Math.max(0, top) });
  };

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
    <div ref={scrollerRef} className="h-full overflow-auto bg-[var(--color-bg-primary)] px-5 py-6 sm:px-8">
      <div className="mx-auto grid max-w-5xl gap-8 lg:grid-cols-[11rem_minmax(0,1fr)]">
        <SettingsToc items={navItems} activeId={activeSection} onSelect={jumpToSection} />
        <div className="min-w-0 max-w-3xl">
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
            {isViewEnabled(features, 'jobs') ? (
              <section id="settings-jobs" className="scroll-mt-16 lg:scroll-mt-4">
                <ScheduledJobsSettingsCard />
              </section>
            ) : null}
            {isSurfaceEnabled(features, 'groupStudio') ? (
              <section id="settings-group-studio" className="scroll-mt-16 lg:scroll-mt-4">
                <GroupStudioSettingsCard />
              </section>
            ) : null}
            <section id="settings-appearance" className="scroll-mt-16 lg:scroll-mt-4">
              <AppearanceSettingsCard theme={theme} onThemeChange={setTheme} />
            </section>
            <section id="settings-models" className="scroll-mt-16 lg:scroll-mt-4">
              <ModelsManager />
            </section>
            <section id="settings-notifications" className="scroll-mt-16 lg:scroll-mt-4">
              <NotificationSettingsCard />
            </section>
            <section id="settings-context" className="scroll-mt-16 lg:scroll-mt-4">
              <ConversationContextSettingsCard />
            </section>
            {window.electronAPI ? (
              <section id="settings-user-data" className="scroll-mt-16 lg:scroll-mt-4">
                <UserDataSettingsCard
                  path={userDataPath}
                  loading={userDataLoading}
                  opening={openingUserData}
                  error={userDataError}
                  onOpen={() => void handleOpenUserData()}
                  onRetry={() => void loadUserDataPath()}
                />
              </section>
            ) : null}
          </div>
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
    <Card styles={{ body: { padding: 0 } }}>
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
