/** 当前服务模式、登录用户和托管能力。主题不放在这里。 */

import { create } from 'zustand';
import { resetTenantState } from '../../stores/resetTenantState';
import {
  DESKTOP_CAPABILITIES,
  EMPTY_CLOUD_ACCOUNT,
  type AuthUser,
  type CloudAccount,
  type ProductCapabilities,
  type ServeMode,
} from './authRules';

type SessionPatch = {
  capabilities?: ProductCapabilities;
  cloudAccount?: CloudAccount;
};

type AuthStore = {
  serveMode: ServeMode;
  user: AuthUser | null;
  loaded: boolean;
  desktopConnected: boolean;
  capabilities: ProductCapabilities;
  cloudAccount: CloudAccount;
  adminOpen: boolean;
  setSession: (
    serveMode: ServeMode,
    user: AuthUser | null,
    desktopConnected?: boolean,
    patch?: SessionPatch,
  ) => void;
  setAdminOpen: (open: boolean) => void;
};

export const useAuthStore = create<AuthStore>((set, get) => ({
  serveMode: 'desktop',
  user: null,
  loaded: false,
  desktopConnected: false,
  capabilities: DESKTOP_CAPABILITIES,
  cloudAccount: EMPTY_CLOUD_ACCOUNT,
  adminOpen: false,
  setSession: (serveMode, user, desktopConnected = false, patch) => {
    const previous = get();
    const previousId = previous.user?.id ?? null;
    const nextId = user?.id ?? null;
    set({
      serveMode,
      user,
      loaded: true,
      desktopConnected,
      capabilities: patch?.capabilities ?? previous.capabilities,
      cloudAccount: patch?.cloudAccount ?? previous.cloudAccount,
    });
    if (previous.loaded && previousId !== nextId) resetTenantState();
  },
  setAdminOpen: (adminOpen) => set({ adminOpen }),
}));
