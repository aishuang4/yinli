/**
 * toolPanels — 工具模块入口元数据。
 */

import { Globe2, Plug, Settings2 } from 'lucide-react';

export type ToolsPanelKey = 'mcp' | 'http' | 'system';

export const TOOL_PANEL_INFO: Record<ToolsPanelKey, {
  label: string;
  description: string;
  icon: typeof Plug;
}> = {
  mcp: {
    label: 'MCP',
    description: '协议化工具服务器和 MCP 工具能力',
    icon: Plug,
  },
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

export const TOOL_PANEL_ORDER: ToolsPanelKey[] = ['mcp', 'http', 'system'];
