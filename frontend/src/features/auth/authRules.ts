/** 托管登录与管理员入口的纯判断，方便单测。 */

export type AuthUser = {
  id: string;
  email: string;
  role: 'user' | 'admin' | string;
  status: string;
};

export type ServeMode = 'desktop' | 'hosted';

export type ProductCapabilities = {
  localProjectPaths: boolean;
  stdioMcp: boolean;
  localExecution: boolean;
  sandboxExecution: boolean;
  sandboxImages: string[];
  cloudLogin: boolean;
  customModels: boolean;
};

export type CloudAccount = {
  connected: boolean;
  email: string;
  status: string;
  baseUrl: string;
};

export const DESKTOP_CAPABILITIES: ProductCapabilities = {
  localProjectPaths: true,
  stdioMcp: true,
  localExecution: true,
  sandboxExecution: false,
  sandboxImages: [],
  cloudLogin: true,
  customModels: true,
};

export const EMPTY_CLOUD_ACCOUNT: CloudAccount = {
  connected: false,
  email: '',
  status: '',
  baseUrl: '',
};

export function workbenchGate(serveMode: ServeMode, user: AuthUser | null): 'workbench' | 'login' {
  if (serveMode === 'desktop') return 'workbench';
  return user ? 'workbench' : 'login';
}

export function showAdminEntry(user: AuthUser | null): boolean {
  return user?.role === 'admin';
}

/** 已登录时不弹本机 Key 引导。Web 登录用户与桌面已连接账号都算登录。 */
export function shouldPromptLocalApiKey(serveMode: ServeMode, desktopConnected: boolean): boolean {
  if (serveMode === 'hosted') return false;
  return !desktopConnected;
}
