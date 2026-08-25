/** 应用内通知与通道设置类型。独立于 Chat SSE。 */

export interface UserNotification {
  id: string;
  workspaceId: string;
  projectId: string;
  jobId: string;
  firingId: string;
  kind: string;
  title: string;
  body: string;
  payload: Record<string, unknown>;
  readAt: string | null;
  createdAt: string;
}

export interface NotificationListResponse {
  items: UserNotification[];
  unreadCount: number;
  nextBefore: string | null;
}

export interface NotificationChannelConfigField {
  key: string;
  label: string;
  input: 'text' | 'url' | 'password' | string;
  placeholder: string;
}

export interface NotificationChannelSettings {
  id: string;
  displayName: string;
  description: string;
  implemented: boolean;
  alwaysOn: boolean;
  enabled: boolean;
  config: Record<string, string>;
  configFields: NotificationChannelConfigField[];
}

export interface NotificationSettingsResponse {
  channels: NotificationChannelSettings[];
}

export interface NotificationStreamEvent {
  type: 'snapshot' | 'notification' | 'read' | 'read_all' | string;
  projectId?: string;
  unreadCount?: number;
  notification?: UserNotification;
  updatedCount?: number;
}

/** Electron 主进程系统通知载荷。 */
export interface DesktopNotificationPayload {
  id: string;
  title: string;
  body: string;
  kind: string;
  jobId: string;
  firingId: string;
}
