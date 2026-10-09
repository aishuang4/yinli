/** 已登录时读取平台模型目录。目录来自接口，不写入本机 models.json。 */

import { useEffect, useState } from 'react';
import { useAuthStore } from '../auth/authStore';
import type { SelectableModel } from './ModelSelector';

export function useProvidedModels(): { models: SelectableModel[]; loggedIn: boolean; source: string } {
  const serveMode = useAuthStore((state) => state.serveMode);
  const user = useAuthStore((state) => state.user);
  const desktopConnected = useAuthStore((state) => state.desktopConnected);
  const loggedIn = serveMode === 'hosted' ? Boolean(user) : desktopConnected;
  const [models, setModels] = useState<SelectableModel[]>([]);
  const [source, setSource] = useState('');

  useEffect(() => {
    if (!loggedIn) {
      setModels([]);
      setSource('');
      return;
    }
    let cancelled = false;
    void fetch('/api/models/provided', { credentials: 'include' })
      .then(async (response) => (response.ok ? response.json() : { models: [] }))
      .then((payload: { models?: SelectableModel[]; source?: string }) => {
        if (!cancelled) {
          setModels(payload.models || []);
          setSource(payload.source || '');
        }
      })
      .catch(() => {
        if (!cancelled) {
          setModels([]);
          setSource('');
        }
      });
    return () => {
      cancelled = true;
    };
  }, [loggedIn]);

  return { models, loggedIn, source };
}
