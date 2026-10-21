/** 切换账号时清掉租户数据。主题和产品功能档保留。 */

import { useAgentsStore } from './agentsStore';
import { useAppStore } from './appStore';
import { useNotificationStore } from './notificationStore';

export function resetTenantState(): void {
  useAppStore.setState({
    currentView: 'chat',
    activeProjectSlug: 'default',
    pendingJobId: null,
    studioSurface: 'none',
    studioTransition: 'idle',
    activeGroupStudioId: null,
    localStudioSeed: null,
  });
  useAgentsStore.setState({ agents: [], loading: false, error: null, revision: 0 });
  useNotificationStore.getState().reset();
}
