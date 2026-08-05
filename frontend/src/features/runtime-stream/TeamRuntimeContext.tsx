/** Team renderer 读取当前 DisplaySnapshot 的只读上下文。 */

import { createContext } from 'react';
import type { DisplaySnapshot } from './protocol';

export interface TeamRuntimeContextValue {
  snapshot: DisplaySnapshot;
  openTeamId: string | null;
  onOpenTeam: (teamBlockId: string, tab?: 'tasks' | 'members' | 'mail') => void;
  openTeamTab: 'tasks' | 'members' | 'mail';
  onCloseTeam: () => void;
}

export const TeamRuntimeContext = createContext<TeamRuntimeContextValue | null>(null);
