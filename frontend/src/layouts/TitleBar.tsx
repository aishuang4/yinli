/**
 * TitleBar — Electron 自定义标题栏组件
 *
 * 在无边框窗口中提供拖拽区域和应用标题显示。
 * macOS 使用 hiddenInset 标题栏样式，红绿灯按钮由系统渲染。
 *
 * 注意：此区域需要设置 -webkit-app-region: drag 来支持窗口拖拽。
 */

export function TitleBar() {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        height: '100%',
        backgroundColor: 'var(--color-bg-secondary)',
        borderBottom: '1px solid var(--color-border)',
        WebkitAppRegion: 'drag', // Electron: 使此区域可拖拽移动窗口
        userSelect: 'none',
        paddingLeft: '80px', // 为 macOS 红绿灯按钮留空间
      } as React.CSSProperties}
    >
      <span
        style={{
          fontSize: 'var(--text-sm)',
          color: 'var(--color-text-secondary)',
          fontWeight: 500,
        }}
      >
        Agent Workstation
      </span>
    </div>
  );
}
