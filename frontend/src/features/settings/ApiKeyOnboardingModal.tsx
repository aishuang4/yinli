/**
 * ApiKeyOnboardingModal — 首次使用时提示用户配置模型 API Key。
 */

import { useEffect, useState } from 'react';
import { Button, Modal, Tag } from '@/components/design';
import { KeyRound, Settings2 } from 'lucide-react';
import { catalogRequest, modelAvailable } from '../models/useModelCatalog';
import { useAppStore } from '../../stores/appStore';

const DISMISSED_STORAGE_KEY = 'agent-workstation.api-key-onboarding.dismissed.v1';


function onboardingDismissed(): boolean {
  try {
    return window.localStorage.getItem(DISMISSED_STORAGE_KEY) === 'true';
  } catch {
    return false;
  }
}

function rememberOnboardingDismissed(): void {
  try {
    window.localStorage.setItem(DISMISSED_STORAGE_KEY, 'true');
  } catch {
    // localStorage 不可用时仍允许用户关闭当前弹窗。
  }
}

export function ApiKeyOnboardingModal() {
  const backendConnected = useAppStore((state) => state.backendConnected);
  const setView = useAppStore((state) => state.setView);
  const setWorkspaceMode = useAppStore((state) => state.setWorkspaceMode);
  const [providerName, setProviderName] = useState('模型供应商');
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!backendConnected || onboardingDismissed()) {
      return;
    }

    let active = true;
    const checkModelSetup = async () => {
      try {
        const catalog = await catalogRequest();
        const model = catalog.models.find(item => item.id === catalog.defaultModelId);
        const provider = catalog.providers.find(item => item.id === model?.providerId);
        if (active && (!model || !modelAvailable(catalog, model))) {
          setProviderName(provider?.name || '模型供应商');
          setOpen(true);
        }
      } catch {
        // 后端健康检查会继续重试，首次引导不阻塞应用启动。
      }
    };

    void checkModelSetup();
    return () => {
      active = false;
    };
  }, [backendConnected]);

  const dismiss = () => {
    rememberOnboardingDismissed();
    setOpen(false);
  };

  const openSettings = () => {
    dismiss();
    setWorkspaceMode('workstation');
    setView('settings');
  };

  return (
    <Modal
      open={open}
      title={null}
      width={460}
      centered
      closable={false}
      maskClosable={false}
      onCancel={dismiss}
      showDivider={false}
      footer={(
        <div className="flex justify-end gap-2">
          <Button onClick={dismiss}>稍后配置</Button>
          <Button type="primary" icon={<Settings2 size={15} />} onClick={openSettings}>
            前往设置
          </Button>
        </div>
      )}
    >
      <div className="flex gap-4 py-2">
        <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg bg-[var(--color-bg-secondary)] text-[var(--color-text-primary)]">
          <KeyRound size={21} strokeWidth={1.8} />
        </div>
        <div className="min-w-0">
          <h2 className="text-base font-semibold text-[var(--color-text-primary)]">配置模型服务</h2>
          <div className="mt-2">
            <Tag size="small" color="warning">{providerName} · 未配置</Tag>
          </div>
          <p className="mt-3 text-sm leading-6 text-[var(--color-text-secondary)]">
            配置 API Key 后即可开始对话和创建 Agent。Key 由当前设备的系统安全凭据库加密保存。
          </p>
        </div>
      </div>
    </Modal>
  );
}
