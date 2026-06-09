/**
 * 应用入口 — React DOM 挂载
 *
 * 引入主题 CSS 和全局样式，挂载 App 根组件。
 * 严格模式开启，便于开发阶段发现潜在问题。
 */

import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';
import './themes/index.css';

// 获取挂载点
const rootElement = document.getElementById('root');
if (!rootElement) {
  throw new Error('Root element #root not found in index.html');
}

// 创建 React 根节点并渲染
createRoot(rootElement).render(
  <StrictMode>
    <App />
  </StrictMode>
);
