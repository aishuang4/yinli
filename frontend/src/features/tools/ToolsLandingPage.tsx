/**
 * ToolsLandingPage — 工具模块第一层入口页。
 *
 * 展示 HTTP 工具和系统工具的管理入口。
 */

import { Plug } from 'lucide-react';
import {
  WorkbenchList,
  WorkbenchListIcon,
  WorkbenchListPage,
  WorkbenchListRow,
  WorkbenchSection,
} from '../../components/workbench';
import { TOOL_PANEL_INFO, TOOL_PANEL_ORDER, type ToolsPanelKey } from './toolPanels';

interface ToolsLandingPageProps {
  onSelectPanel: (panel: ToolsPanelKey) => void;
}

export function ToolsLandingPage({ onSelectPanel }: ToolsLandingPageProps) {
  return (
    <WorkbenchListPage title="工具" icon={<Plug size={18} />}>
      <WorkbenchSection title="工具入口" compact>
        <WorkbenchList>
          {TOOL_PANEL_ORDER.map((key, index) => {
            const item = TOOL_PANEL_INFO[key];
            const Icon = item.icon;
            return (
              <WorkbenchListRow
                key={key}
                icon={(
                  <WorkbenchListIcon>
                    <Icon size={20} strokeWidth={1.8} />
                  </WorkbenchListIcon>
                )}
                title={item.label}
                subtitle={item.description}
                last={index === TOOL_PANEL_ORDER.length - 1}
                onSelect={() => onSelectPanel(key)}
              />
            );
          })}
        </WorkbenchList>
      </WorkbenchSection>
    </WorkbenchListPage>
  );
}
