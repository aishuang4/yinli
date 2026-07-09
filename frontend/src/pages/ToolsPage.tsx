/**
 * ToolsPage — 工具模块路由页。
 *
 * 第一层展示工具域入口；进入二级页后分别管理 MCP、HTTP 工具和系统工具。
 */

import { useState } from 'react';
import { WorkbenchDetailShell } from '../components/workbench';
import { HttpToolsPanel } from '../features/tools/HttpToolsPanel';
import { SystemToolsPanel } from '../features/tools/SystemToolsPanel';
import { ToolsLandingPage } from '../features/tools/ToolsLandingPage';
import { TOOL_PANEL_INFO, type ToolsPanelKey } from '../features/tools/toolPanels';
import { MCPPage } from './MCPPage';

export function ToolsPage() {
  const [activePanel, setActivePanel] = useState<ToolsPanelKey | null>(null);

  if (!activePanel) {
    return <ToolsLandingPage onSelectPanel={setActivePanel} />;
  }

  const panel = TOOL_PANEL_INFO[activePanel];

  return (
    <WorkbenchDetailShell
      title={panel.label}
      subtitle={panel.description}
      onBack={() => setActivePanel(null)}
    >
      {activePanel === 'mcp' ? <MCPPage embedded /> : null}
      {activePanel === 'http' ? <HttpToolsPanel /> : null}
      {activePanel === 'system' ? <SystemToolsPanel /> : null}
    </WorkbenchDetailShell>
  );
}
