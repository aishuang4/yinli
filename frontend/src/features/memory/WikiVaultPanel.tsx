/**
 * WikiVaultPanel — Memory Wiki Vault 同步和本地打开入口。
 */

import { ExternalLink, FileText, FolderOpen, FolderSync } from 'lucide-react';
import { Button, Empty, Spinner } from '@agentscope-ai/design';
import type { MemoryWikiSyncResult } from '../../types/memory';
import { notifyError, notifySuccess } from '../../utils/feedback';
import { DetailSection, MetricBlock } from './MemoryDetailBlocks';

interface WikiVaultPanelProps {
  result: MemoryWikiSyncResult | null;
  syncing: boolean;
  onSync: () => Promise<void>;
}

export function WikiVaultPanel({
  result,
  syncing,
  onSync,
}: WikiVaultPanelProps) {
  const canOpenLocal = Boolean(window.electronAPI);

  async function openVaultPath() {
    if (!result || !window.electronAPI) return;
    const response = await window.electronAPI.openPath(result.path);
    if (response.ok) {
      notifySuccess('已打开 Wiki 文件夹');
    } else {
      notifyError(`打开 Wiki 文件夹失败：${response.error || '未知错误'}`);
    }
  }

  async function openObsidianVault() {
    if (!result || !window.electronAPI) return;
    const url = `obsidian://open?path=${encodeURIComponent(result.path)}`;
    const response = await window.electronAPI.openExternal(url);
    if (response.ok) {
      notifySuccess('已请求 Obsidian 打开 Vault');
    } else {
      notifyError(`打开 Obsidian 失败：${response.error || '未知错误'}`);
    }
  }

  return (
    <section className="grid h-full min-h-0 grid-rows-[auto_1fr] overflow-hidden p-5">
      <div className="flex items-start justify-between gap-4 rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-sm font-semibold text-[var(--color-text-primary)]">
            <FolderSync size={16} color="var(--color-accent)" />
            Obsidian-compatible Memory Wiki
          </div>
          <p className="mt-1 text-xs leading-5 text-[var(--color-text-secondary)]">
            从 Markdown 记忆、历史记忆和已注册 Skills 生成只读 wiki 视图。运行时仍以原始存储为事实源。
          </p>
        </div>
        <Button type="primary" icon={<FolderSync size={14} />} onClick={() => void onSync()} loading={syncing}>
          同步 Wiki
        </Button>
      </div>

      <div className="mt-5 min-h-0 overflow-y-auto rounded-md border border-[var(--color-border)]">
        <div className="border-b border-[var(--color-border)] px-4 py-3 text-sm font-medium text-[var(--color-text-primary)]">
          同步结果
        </div>
        {syncing ? (
          <div className="flex justify-center py-12">
            <Spinner tip="同步 Memory Wiki..." />
          </div>
        ) : !result ? (
          <div className="p-6">
            <Empty type="noData" description="尚未同步 Wiki Vault" />
          </div>
        ) : (
          <div className="space-y-4 p-4">
            <div className="grid grid-cols-5 gap-3">
              <MetricBlock label="写入文件" value={String(result.filesWritten)} />
              <MetricBlock label="Markdown 记忆" value={String(result.markdownMemories)} />
              <MetricBlock label="历史记忆" value={String(result.historyNodes)} />
              <MetricBlock label="Skills" value={String(result.skills)} />
              <MetricBlock label="实体" value={String(result.entities)} />
            </div>

            <DetailSection title="Vault 路径" icon={<FolderSync size={14} />}>
              <div className="break-all font-mono text-xs">{result.path}</div>
              <div className="mt-3 flex flex-wrap gap-2">
                <Button icon={<FolderOpen size={14} />} onClick={() => void openVaultPath()} disabled={!canOpenLocal}>
                  打开文件夹
                </Button>
                <Button icon={<ExternalLink size={14} />} onClick={() => void openObsidianVault()} disabled={!canOpenLocal}>
                  在 Obsidian 打开
                </Button>
              </div>
              <div className="mt-2 text-xs leading-5 text-[var(--color-text-tertiary)]">
                {canOpenLocal
                  ? 'Obsidian 第一次使用时需要先把该文件夹注册为 vault。'
                  : '当前浏览器环境无法打开本地路径；桌面应用中可直接打开文件夹或 Obsidian。'}
              </div>
            </DetailSection>

            <DetailSection title="生成文件" icon={<FileText size={14} />}>
              {result.files.length === 0 ? (
                <div className="text-xs text-[var(--color-text-tertiary)]">暂无文件。</div>
              ) : (
                <div className="max-h-[360px] overflow-y-auto rounded-md bg-[var(--color-bg-primary)] p-2">
                  {result.files.slice(0, 120).map((file) => (
                    <div key={file} className="border-b border-[var(--color-border)] py-1.5 font-mono text-xs text-[var(--color-text-secondary)] last:border-b-0">
                      {file}
                    </div>
                  ))}
                  {result.files.length > 120 && (
                    <div className="py-2 text-xs text-[var(--color-text-tertiary)]">
                      还有 {result.files.length - 120} 个文件未显示。
                    </div>
                  )}
                </div>
              )}
            </DetailSection>
          </div>
        )}
      </div>
    </section>
  );
}
