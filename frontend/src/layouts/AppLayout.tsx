/**
 * AppLayout — 全局应用布局组件
 *
 * 使用 CSS Grid 实现多面板 IDE 风格布局。
 * 包含 TitleBar / ActivityBar / MainContent / DetailPanel / StatusBar。
 * DetailPanel 可通过 useAppStore 控制展开/收起。
 *
 * @see 前端设计文档 §1.1 整体布局架构
 */

import { type ReactNode } from 'react';
import { useAppStore } from '../stores/appStore';
import { TitleBar } from './TitleBar';
import { ActivityBar } from './ActivityBar';
import { StatusBar } from './StatusBar';
import './AppLayout.css';

interface AppLayoutProps {
  /** 主内容区域渲染的内容 */
  children: ReactNode;
  /** 详情面板渲染的内容（可选） */
  detail?: ReactNode;
}

/**
 * AppLayout 组件
 * 应用根布局，管理多面板的 Grid 结构
 */
export function AppLayout({ children, detail }: AppLayoutProps) {
  const detailPanelOpen = useAppStore((s) => s.detailPanelOpen);

  return (
    <div className="app-layout" data-detail-open={detailPanelOpen}>
      {/* 自定义标题栏（Electron 无边框窗口） */}
      <div className="app-layout__titlebar">
        <TitleBar />
      </div>

      {/* 左侧活动导航栏 */}
      <div className="app-layout__activity-bar">
        <ActivityBar />
      </div>

      {/* 主内容区域 */}
      <main className="app-layout__main">
        {children}
      </main>

      {/* 右侧详情面板（可折叠） */}
      {detailPanelOpen && detail && (
        <aside className="app-layout__detail">
          {detail}
        </aside>
      )}

      {/* 底部状态栏 */}
      <div className="app-layout__statusbar">
        <StatusBar />
      </div>
    </div>
  );
}
