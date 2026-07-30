/**
 * DesktopBackendGate — 桌面端内置服务启动与故障恢复界面。
 *
 * Electron 安装包从本地文件加载，因此即使 Python 后端尚未就绪或异常退出，
 * 仍能提供可理解的状态与重试入口，而不是展示半初始化的工作台。
 */

import { Button, Spinner } from '@/components/design';
import { FolderOpen, RefreshCw, ServerCrash } from 'lucide-react';
import type { DesktopBackendStatus } from '../types/desktopBackend';

interface DesktopBackendGateProps {
  status: DesktopBackendStatus;
  onRetry: () => void;
}

export function DesktopBackendGate({ status, onRetry }: DesktopBackendGateProps) {
  const starting = status.state === 'starting';

  return (
    <main className="desktop-backend-gate">
      <section className="desktop-backend-gate__content" aria-live="polite">
        <div className="desktop-backend-gate__mark" aria-hidden="true">
          {starting ? <Spinner /> : <ServerCrash size={28} strokeWidth={1.6} />}
        </div>
        <h1>{starting ? '正在启动本地服务' : '本地服务无法启动'}</h1>
        <p>{status.detail || '正在准备工作台所需的本地服务…'}</p>
        {!starting && (
          <div className="desktop-backend-gate__actions">
            <Button onClick={() => void window.electronAPI?.openDesktopLogs()} icon={<FolderOpen size={15} />}>
              查看日志
            </Button>
            <Button type="primary" icon={<RefreshCw size={15} />} onClick={onRetry}>
              重试启动
            </Button>
          </div>
        )}
      </section>
    </main>
  );
}
