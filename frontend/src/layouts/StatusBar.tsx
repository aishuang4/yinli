/**
 * StatusBar — 底部状态栏组件
 *
 * 显示后端连接状态、当前模型、Token 用量等信息。
 * 固定在窗口底部，高度 28px。
 */

import { useAppStore } from '../stores/appStore';
import { useSettingsStore } from '../stores/settingsStore';

/**
 * StatusBar 组件
 */
export function StatusBar() {
  const backendConnected = useAppStore((s) => s.backendConnected);
  const backendUrl = useSettingsStore((s) => s.backendUrl);
  const appVersion = useSettingsStore((s) => s.appVersion);

  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        height: '100%',
        padding: '0 12px',
        backgroundColor: 'var(--color-bg-secondary)',
        borderTop: '1px solid var(--color-border)',
        fontSize: 'var(--text-xs)',
        color: 'var(--color-text-tertiary)',
        userSelect: 'none',
      }}
    >
      {/* 左侧：连接状态 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
        {/* 连接状态指示灯 */}
        <span
          style={{
            display: 'inline-block',
            width: '8px',
            height: '8px',
            borderRadius: '50%',
            backgroundColor: backendConnected
              ? 'var(--color-success)'
              : 'var(--color-error)',
          }}
          aria-label={backendConnected ? '已连接' : '未连接'}
        />
        <span>
          {backendConnected ? '已连接' : '未连接'}
        </span>
        <span style={{ color: 'var(--color-text-tertiary)' }}>
          {backendUrl}
        </span>
      </div>

      {/* 右侧：版本信息 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
        <span>v{appVersion}</span>
      </div>
    </div>
  );
}
