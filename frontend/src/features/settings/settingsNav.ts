/** 系统设置页目录：按产品档和运行环境裁剪可见锚点。 */

export type SettingsNavItem = {
  id: string;
  label: string;
};

const ALL_SETTINGS_NAV: Array<SettingsNavItem & { key: 'jobs' | 'groupStudio' | 'userData' | 'always' }> = [
  { id: 'settings-jobs', label: '定时任务', key: 'jobs' },
  { id: 'settings-group-studio', label: '群聊工作室', key: 'groupStudio' },
  { id: 'settings-appearance', label: '界面外观', key: 'always' },
  { id: 'settings-notifications', label: '消息通知', key: 'always' },
  { id: 'settings-user-data', label: '用户数据', key: 'userData' },
];

export function visibleSettingsNavItems(flags: {
  jobs: boolean;
  groupStudio: boolean;
  userData: boolean;
}): SettingsNavItem[] {
  return ALL_SETTINGS_NAV.filter((item) => {
    if (item.key === 'jobs') return flags.jobs;
    if (item.key === 'groupStudio') return flags.groupStudio;
    if (item.key === 'userData') return flags.userData;
    return true;
  }).map(({ id, label }) => ({ id, label }));
}
