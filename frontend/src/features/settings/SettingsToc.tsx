/** SettingsToc — 设置页锚点目录，点击跳到对应区块。 */

import type { SettingsNavItem } from './settingsNav';

export function SettingsToc({
  items,
  activeId,
  onSelect,
  variant,
}: {
  items: SettingsNavItem[];
  activeId: string;
  onSelect: (id: string) => void;
  variant: 'chips' | 'rail';
}) {
  if (variant === 'chips') {
    return (
      <nav
        data-layout="chips"
        className="sticky top-0 z-10 -mx-5 mb-4 flex gap-2 overflow-x-auto border-b border-[var(--color-border)] bg-[var(--color-bg-primary)] px-5 py-2 lg:hidden"
        aria-label="设置目录"
      >
        {items.map((item) => {
          const active = item.id === activeId;
          return (
            <button
              key={item.id}
              type="button"
              onClick={() => onSelect(item.id)}
              data-settings-anchor={item.id}
              aria-current={active ? 'location' : undefined}
              className={`shrink-0 rounded-full border px-3 py-1 text-xs transition ${
                active
                  ? 'border-[var(--color-accent)] bg-[var(--color-accent-bg)] text-[var(--color-accent-text)]'
                  : 'border-[var(--color-border)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]'
              }`}
            >
              {item.label}
            </button>
          );
        })}
      </nav>
    );
  }

  return (
    <nav data-layout="rail" className="sticky top-6 hidden self-start lg:block" aria-label="设置目录">
      <div className="mb-2 text-xs font-medium text-[var(--color-text-tertiary)]">目录</div>
      <ul className="space-y-0.5 border-l border-[var(--color-border)]">
        {items.map((item) => {
          const active = item.id === activeId;
          return (
            <li key={item.id}>
              <button
                type="button"
                onClick={() => onSelect(item.id)}
                data-settings-anchor={item.id}
                aria-current={active ? 'location' : undefined}
                className={`-ml-px block w-full border-l-2 px-3 py-1.5 text-left text-sm transition ${
                  active
                    ? 'border-[var(--color-accent)] text-[var(--color-text-primary)]'
                    : 'border-transparent text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]'
                }`}
              >
                {item.label}
              </button>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
