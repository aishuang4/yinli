/** 当前服务模式和登录用户。管理员页开关也放在这里。 */

import { create } from 'zustand';
import type { AuthUser, ServeMode } from './authRules';

type AuthStore = {
  serveMode: ServeMode;
  user: AuthUser | null;
  loaded: boolean;
  desktopConnected: boolean;
  adminOpen: boolean;
  setSession: (serveMode: ServeMode, user: AuthUser | null, desktopConnected?: boolean) => void;
  setAdminOpen: (open: boolean) => void;
};

export const useAuthStore = create<AuthStore>((set) => ({
  serveMode: 'desktop',
  user: null,
  loaded: false,
  desktopConnected: false,
  adminOpen: false,
  setSession: (serveMode, user, desktopConnected = false) => set({
    serveMode,
    user,
    loaded: true,
    desktopConnected,
  }),
  setAdminOpen: (adminOpen) => set({ adminOpen }),
}));
