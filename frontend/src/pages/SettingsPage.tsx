/**
 * SettingsPage — 设置页面（占位）
 *
 * 后续实现：模型配置、安全边界、存储设置等
 */

import { Settings } from 'lucide-react';

export function SettingsPage() {
  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        height: '100%',
        color: 'var(--color-text-tertiary)',
        gap: '12px',
      }}
    >
      <Settings size={48} strokeWidth={1} color="var(--color-text-tertiary)" />
      <div style={{ fontSize: 'var(--text-xl)', fontWeight: 500, color: 'var(--color-text-primary)' }}>
        设置
      </div>
      <div style={{ fontSize: 'var(--text-sm)' }}>
        配置模型、安全策略和系统参数
      </div>
    </div>
  );
}
