/** 设置页目录按产品档裁剪可见锚点。 */

import { describe, expect, it } from 'vitest';
import { visibleSettingsNavItems } from './settingsNav';

describe('visibleSettingsNavItems', () => {
  it('始终包含外观、通知和对话上下文', () => {
    const items = visibleSettingsNavItems({ jobs: false, groupStudio: false, userData: false });
    expect(items.map((item) => item.id)).toEqual([
      'settings-appearance',
      'settings-notifications',
      'settings-context',
    ]);
  });

  it('打开产品档和桌面端时补上对应锚点', () => {
    const items = visibleSettingsNavItems({ jobs: true, groupStudio: true, userData: true });
    expect(items.map((item) => item.id)).toEqual([
      'settings-jobs',
      'settings-group-studio',
      'settings-appearance',
      'settings-notifications',
      'settings-context',
      'settings-user-data',
    ]);
  });
});
