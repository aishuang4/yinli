/** UserDataSettingsCard — 展示桌面端用户数据目录，并提供系统文件管理器打开入口。 */

import { Button, Card } from '@/components/design';
import { FolderOpen, RefreshCw } from 'lucide-react';

export function UserDataSettingsCard({
  path,
  loading,
  opening,
  error,
  onOpen,
  onRetry,
}: {
  path: string | null;
  loading: boolean;
  opening: boolean;
  error: string;
  onOpen: () => void;
  onRetry: () => void;
}) {
  return (
    <Card bodyStyle={{ padding: 0 }}>
      <div className="p-5">
        <div className="text-sm font-medium text-[var(--color-text-primary)]">用户数据</div>
        <p className="mt-2 text-xs leading-5 text-[var(--color-text-tertiary)]">
          对话、配置、Memory、日志和本地密钥保存在这里。
        </p>
        <div className="mt-4 space-y-3">
          <div>
            <div className="text-xs font-medium text-[var(--color-text-secondary)]">用户数据目录</div>
            {loading ? (
              <div className="mt-2 text-xs text-[var(--color-text-tertiary)]">读取中...</div>
            ) : error ? (
              <div className="mt-2 flex items-center justify-between gap-2">
                <span className="text-xs text-[var(--color-error)]">暂时无法读取目录</span>
                <Button
                  size="small"
                  type="text"
                  icon={<RefreshCw size={14} />}
                  aria-label="重试读取用户数据目录"
                  title="重试"
                  onClick={onRetry}
                />
              </div>
            ) : (
              <div
                className="mt-2 break-all rounded-md bg-[var(--color-bg-secondary)] px-2.5 py-2 text-xs leading-5 text-[var(--color-text-secondary)]"
                title={path || undefined}
              >
                {path || '未找到用户数据目录'}
              </div>
            )}
          </div>
          <Button
            className="w-full justify-center"
            size="small"
            icon={<FolderOpen size={14} />}
            loading={opening}
            disabled={!path || loading || Boolean(error)}
            aria-label="打开用户数据目录"
            onClick={onOpen}
          >
            打开目录
          </Button>
        </div>
      </div>
    </Card>
  );
}
