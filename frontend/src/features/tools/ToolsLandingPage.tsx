/**
 * ToolsLandingPage — 工具模块第一层入口页。
 *
 * 将 MCP、HTTP 工具和系统工具收束成统一列表入口，具体配置进入二级页面。
 */

import { Plug } from 'lucide-react';
import { Tag } from '@agentscope-ai/design';
import {
  WorkbenchList,
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
                  <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-black text-[var(--color-accent)]">
                    <Icon size={20} strokeWidth={1.8} />
                  </span>
                )}
                title={item.label}
                subtitle={item.description}
                description="进入后查看和配置具体工具"
                last={index === TOOL_PANEL_ORDER.length - 1}
                onSelect={() => onSelectPanel(key)}
                meta={<Tag size="small" color="transparent">二级页面</Tag>}
              />
            );
          })}
        </WorkbenchList>
      </WorkbenchSection>
    </WorkbenchListPage>
  );
}
