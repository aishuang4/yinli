/** 服务端审核模型是否已配置并可解析。 */
import { useCallback, useEffect, useState } from 'react';

export interface PermissionReviewStatus {
  available: boolean;
  modelConfigId?: string;
  reason: string;
}

export function usePermissionReviewStatus() {
  const [status, setStatus] = useState<PermissionReviewStatus>({ available: false, reason: '正在检查审核模型…' });
  const reload = useCallback(async () => {
    try {
      const response = await fetch('/api/settings/models/permission-review/status');
      if (!response.ok) throw new Error('无法读取审核模型状态');
      const value: PermissionReviewStatus = await response.json();
      setStatus(value);
    } catch {
      setStatus({ available: false, reason: '无法确认审核模型，请到模型页检查连接。' });
    }
  }, []);
  useEffect(() => {
    void reload();
    const refresh = () => { void reload(); };
    window.addEventListener('agent-workstation:model-settings-updated', refresh);
    window.addEventListener('focus', refresh);
    return () => {
      window.removeEventListener('agent-workstation:model-settings-updated', refresh);
      window.removeEventListener('focus', refresh);
    };
  }, [reload]);
  return { status, reload };
}
