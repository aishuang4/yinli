/** 桌面端内置 Python 后端的生命周期状态。 */
export interface DesktopBackendStatus {
  state: 'starting' | 'ready' | 'error';
  detail?: string;
}
