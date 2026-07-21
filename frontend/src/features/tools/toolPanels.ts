/**
 * toolPanels — 工具模块入口元数据。
 */

import { Globe2, Settings2 } from 'lucide-react';

export type ToolsPanelKey = 'http' | 'system';

export const TOOL_PANEL_INFO: Record<ToolsPanelKey, {
  label: string;
  description: string;
  icon: typeof Globe2;
}> = {
  http: {
    label: 'HTTP 工具',
    description: '把传统系统 API 封装成 Agent 可调用工具',
    icon: Globe2,
  },
  system: {
    label: '系统工具',
    description: '内置文件、命令和人工交互工具能力',
    icon: Settings2,
  },
};

export const TOOL_PANEL_ORDER: ToolsPanelKey[] = ['http', 'system'];
