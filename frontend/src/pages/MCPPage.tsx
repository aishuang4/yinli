/**
 * MCPPage — MCP Server 管理页面。
 *
 * 第一层展示 Server 列表；点击 Server 后进入第二层查看工具和连接配置。
 */

import { useMemo, useState } from 'react';
import { List, Plus, Plug, RefreshCw, Trash2, Wrench } from 'lucide-react';
import { Button, Modal, Tag } from '@/components/design';
import { McpRegisterModal, type McpRegisterFormState } from '../features/mcp/McpRegisterModal';
import { ToggleButton } from '../components/ToggleButton';
import { CatalogFilterBar, type CatalogInstallFilter } from '../components/form/CatalogFilterBar';
import {
  WorkbenchDetailShell,
  WorkbenchEmptyPanel,
  WorkbenchList,
  WorkbenchListIcon,
  WorkbenchListPage,
  WorkbenchListRow,
  WorkbenchLoadingPanel,
  WorkbenchSection,
} from '../components/workbench';
import { useMcp } from '../hooks/useMcp';
import { useAuthStore } from '../features/auth/authStore';
import type { McpServer } from '../types/mcp';

interface MCPPageProps {
  embedded?: boolean;
}

function serverMatchesQuery(server: McpServer, query: string): boolean {
  const normalized = query.trim().toLowerCase();
  if (!normalized) return true;

  return [
    server.name,
    server.description,
    server.category,
    server.transport,
    ...server.tags,
    ...server.tools.flatMap((tool) => [tool.name, tool.description]),
  ].some((value) => value.toLowerCase().includes(normalized));
}

export function MCPPage({ embedded = false }: MCPPageProps) {
  const {
    servers,
    categories,
    loading,
    error,
    refresh,
    registerServer,
    installServer,
    uninstallServer,
    deleteServer,
    setServerEnabled,
  } = useMcp();

  const [query, setQuery] = useState('');
  const [category, setCategory] = useState('all');
  const [installFilter, setInstallFilter] = useState<CatalogInstallFilter>('all');
  const [selectedServer, setSelectedServer] = useState<McpServer | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [deleteConfirm, setDeleteConfirm] = useState<McpServer | null>(null);
  const [registerOpen, setRegisterOpen] = useState(false);
  const [registering, setRegistering] = useState(false);
  const [registerError, setRegisterError] = useState<string | null>(null);
  const [registerForm, setRegisterForm] = useState<McpRegisterFormState>({
    name: '',
    url: '',
    description: '',
    headers: [{ key: '', value: '' }],
  });

  const stdioMcp = useAuthStore((state) => state.capabilities.stdioMcp);
  const visibleServers = stdioMcp ? servers : servers.filter((server) => server.transport !== 'stdio');
  const filteredServers = useMemo(() => (
    visibleServers.filter((server) => {
      if (!serverMatchesQuery(server, query)) return false;
      if (category !== 'all' && server.category !== category) return false;
      if (installFilter === 'installed') return server.status === 'installed';
      if (installFilter === 'available') return server.status !== 'installed';
      return true;
    })
  ), [visibleServers, query, category, installFilter]);

  const installedCount = visibleServers.filter((server) => server.status === 'installed').length;
  const enabledToolCount = visibleServers
    .filter((server) => server.status === 'installed' && server.enabled)
    .reduce((total, server) => total + server.tools.length, 0);

  async function runServerAction(serverId: string, action: () => Promise<McpServer>) {
    try {
      setBusyId(serverId);
      const next = await action();
      setSelectedServer((current) => (current?.id === next.id ? next : current));
    } finally {
      setBusyId(null);
    }
  }

  async function handleRegister() {
    if (!registerForm.name.trim() || !registerForm.url.trim()) {
      setRegisterError('请输入 MCP 名称和地址');
      return;
    }

    try {
      setRegistering(true);
      setRegisterError(null);
      const headers = registerForm.headers.reduce<Record<string, string>>((result, header) => {
        const key = header.key.trim();
        const value = header.value.trim();
        if (key && value) result[key] = value;
        return result;
      }, {});
      const server = await registerServer({
        name: registerForm.name.trim(),
        url: registerForm.url.trim(),
        description: registerForm.description.trim() || undefined,
        headers,
      });
      setRegisterOpen(false);
      setRegisterForm({
        name: '',
        url: '',
        description: '',
        headers: [{ key: '', value: '' }],
      });
      setSelectedServer(server);
    } catch (err) {
      setRegisterError(err instanceof Error ? err.message : '注册 MCP 失败');
    } finally {
      setRegistering(false);
    }
  }

  async function handleDeleteServer() {
    if (!deleteConfirm) return;
    try {
      setBusyId(deleteConfirm.id);
      await deleteServer(deleteConfirm.id);
      setSelectedServer((current) => (current?.id === deleteConfirm.id ? null : current));
      setDeleteConfirm(null);
    } finally {
      setBusyId(null);
    }
  }

  const actions = (
    <>
      <Tag size="small" color="blue">{servers.length} 个 Server</Tag>
      <Tag size="small" color="green">{installedCount} 个已安装</Tag>
      <Tag size="small" color="transparent">{enabledToolCount} 个工具</Tag>
      <Button
        type="primary"
        icon={<Plus size={14} />}
        onClick={() => {
          setRegisterError(null);
          setRegisterOpen(true);
        }}
      >
        注册 MCP
      </Button>
      <Button icon={<RefreshCw size={14} />} onClick={refresh} loading={loading}>
        刷新
      </Button>
    </>
  );

  const modals = (
    <>
      <Modal
        title="删除 MCP Server"
        open={Boolean(deleteConfirm)}
        onCancel={() => setDeleteConfirm(null)}
        onOk={handleDeleteServer}
        confirmLoading={Boolean(deleteConfirm && busyId === deleteConfirm.id)}
        okText="删除"
        cancelText="取消"
        okButtonProps={{ danger: true }}
      >
        确定要删除「{deleteConfirm?.name}」吗？内置 MCP 不能删除，只能卸载或停用。
      </Modal>

      <McpRegisterModal
        open={registerOpen}
        form={registerForm}
        error={registerError}
        registering={registering}
        onFormChange={setRegisterForm}
        onCancel={() => setRegisterOpen(false)}
        onRegister={() => void handleRegister()}
      />
    </>
  );

  if (selectedServer) {
    const installed = selectedServer.status === 'installed';
    const busy = busyId === selectedServer.id;
    return (
      <>
        <WorkbenchDetailShell
          title={selectedServer.name}
          subtitle={selectedServer.description || '暂无描述'}
          onBack={() => setSelectedServer(null)}
          actions={(
            <>
              {installed ? (
                <ToggleButton
                  checked={selectedServer.enabled}
                  checkedLabel="启用中"
                  uncheckedLabel="已停用"
                  disabled={busy}
                  ariaLabel={`${selectedServer.name} 启停状态`}
                  onChange={(enabled) => runServerAction(
                    selectedServer.id,
                    () => setServerEnabled(selectedServer.id, enabled),
                  )}
                />
              ) : null}
              {installed ? (
                <Button
                  size="small"
                  danger
                  loading={busy}
                  onClick={() => runServerAction(selectedServer.id, () => uninstallServer(selectedServer.id))}
                >
                  卸载
                </Button>
              ) : (
                <Button
                  size="small"
                  type="primary"
                  loading={busy}
                  onClick={() => runServerAction(selectedServer.id, () => installServer(selectedServer.id))}
                >
                  安装
                </Button>
              )}
              <Button size="small" danger icon={<Trash2 size={13} />} onClick={() => setDeleteConfirm(selectedServer)}>
                删除
              </Button>
            </>
          )}
        >
          <McpServerDetail server={selectedServer} />
        </WorkbenchDetailShell>
        {modals}
      </>
    );
  }

  const content = (
    <>
      <div className="mb-5">
        <CatalogFilterBar
          query={query}
          category={category}
          installFilter={installFilter}
          categories={categories}
          searchPlaceholder="搜索 Server、工具或标签"
          onQueryChange={setQuery}
          onCategoryChange={setCategory}
          onInstallFilterChange={setInstallFilter}
        />
      </div>

      <WorkbenchSection title="MCP Server" compact>
        {error ? (
          <WorkbenchEmptyPanel>
            <span className="text-[var(--color-error)]">{error}</span>
          </WorkbenchEmptyPanel>
        ) : loading ? (
          <WorkbenchLoadingPanel tip="加载 MCP Server..." />
        ) : filteredServers.length === 0 ? (
          <WorkbenchEmptyPanel>没有匹配的 MCP Server</WorkbenchEmptyPanel>
        ) : (
          <WorkbenchList>
            {filteredServers.map((server, index) => (
              <McpServerRow
                key={server.id}
                server={server}
                busy={busyId === server.id}
                last={index === filteredServers.length - 1}
                onSelect={() => setSelectedServer(server)}
                onInstall={() => runServerAction(server.id, () => installServer(server.id))}
                onUninstall={() => runServerAction(server.id, () => uninstallServer(server.id))}
                onDelete={() => setDeleteConfirm(server)}
              />
            ))}
          </WorkbenchList>
        )}
      </WorkbenchSection>
    </>
  );

  if (embedded) {
    return (
      <div className="h-full min-h-0 overflow-auto bg-[var(--color-bg-primary)]">
        <main className="mx-auto w-full max-w-5xl px-5 py-4">
          <div className="mb-5 flex flex-wrap items-center justify-end gap-2">{actions}</div>
          {content}
        </main>
        {modals}
      </div>
    );
  }

  return (
    <>
      <WorkbenchListPage title="MCP" icon={<Plug size={18} />} actions={actions}>
        {content}
      </WorkbenchListPage>
      {modals}
    </>
  );
}

function McpServerRow({
  server,
  busy,
  last,
  onSelect,
  onInstall,
  onUninstall,
  onDelete,
}: {
  server: McpServer;
  busy: boolean;
  last: boolean;
  onSelect: () => void;
  onInstall: () => void;
  onUninstall: () => void;
  onDelete: () => void;
}) {
  const installed = server.status === 'installed';
  return (
    <WorkbenchListRow
      icon={(
        <WorkbenchListIcon>
          <Plug size={20} strokeWidth={1.8} />
        </WorkbenchListIcon>
      )}
      title={server.name}
      subtitle={`${server.category} · ${server.transport}`}
      description={server.description || server.tools.map((tool) => tool.name).join(' / ') || '暂无描述'}
      last={last}
      layout="columns"
      onSelect={onSelect}
      meta={(
        <>
          <Tag size="small" color={installed ? 'green' : 'blue'}>{installed ? '已安装' : '可安装'}</Tag>
          <Tag size="small" color={installed && server.enabled ? 'green' : 'transparent'}>
            {installed ? (server.enabled ? '已启用' : '已停用') : `${server.tools.length} 工具`}
          </Tag>
        </>
      )}
      trailing={(
        <>
          <button
            type="button"
            onClick={onSelect}
            className="icon-button h-8 w-8 rounded-md text-[var(--color-text-tertiary)] transition hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]"
            aria-label={`打开 ${server.name} 详情`}
          >
            <List size={17} />
          </button>
          {installed ? (
            <Button size="small" danger loading={busy} onClick={onUninstall}>
              卸载
            </Button>
          ) : (
            <Button size="small" type="primary" loading={busy} onClick={onInstall}>
              安装
            </Button>
          )}
          <button
            type="button"
            onClick={onDelete}
            className="icon-button h-8 w-8 rounded-md text-[var(--color-text-tertiary)] transition hover:bg-[var(--color-error-bg)] hover:text-[var(--color-error)]"
            aria-label={`删除 ${server.name}`}
          >
            <Trash2 size={15} />
          </button>
        </>
      )}
    />
  );
}

function McpServerDetail({ server }: { server: McpServer }) {
  return (
    <div className="h-full min-h-0 overflow-y-auto p-5">
      <div className="mx-auto grid w-full max-w-5xl gap-4">
        <section className="rounded-xl bg-[var(--color-bg-primary)] p-4">
          <div className="grid gap-3 text-sm md:grid-cols-2">
            <DetailField label="分类" value={server.category} />
            <DetailField label="通信方式" value={server.transport} />
            <DetailField label="状态" value={server.status === 'installed' ? (server.enabled ? '已启用' : '已停用') : '未安装'} />
            <DetailField label="工具数量" value={`${server.tools.length}`} />
          </div>
        </section>

        <section className="rounded-xl bg-[var(--color-bg-primary)] p-4">
          <div className="mb-3 text-sm font-semibold text-[var(--color-text-primary)]">启动命令</div>
          <code className="block rounded-md bg-[var(--color-bg-secondary)] px-3 py-2 text-xs text-[var(--color-text-primary)]">
            {server.command || server.url || '未配置'}
          </code>
        </section>

        {Object.keys(server.headers).length > 0 && (
          <section className="rounded-xl bg-[var(--color-bg-primary)] p-4">
            <div className="mb-3 text-sm font-semibold text-[var(--color-text-primary)]">请求头</div>
            <div className="space-y-1 rounded-md bg-[var(--color-bg-secondary)] px-3 py-2 text-xs text-[var(--color-text-primary)]">
              {Object.keys(server.headers).map((key) => (
                <div key={key}>{key}: ********</div>
              ))}
            </div>
          </section>
        )}

        <section className="rounded-xl bg-[var(--color-bg-primary)] p-4">
          <div className="mb-3 flex items-center gap-2 text-sm font-medium text-[var(--color-text-primary)]">
            <Wrench size={16} />
            提供的工具
          </div>
          <div className="space-y-2">
            {server.tools.map((tool) => (
              <div key={tool.id} className="rounded-md border border-[var(--color-border)] p-3">
                <div className="font-medium text-[var(--color-text-primary)]">{tool.name}</div>
                <div className="mt-1 text-sm text-[var(--color-text-secondary)]">{tool.description}</div>
                <code className="mt-2 block text-xs text-[var(--color-text-tertiary)]">{tool.id}</code>
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}

function DetailField({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md bg-[var(--color-bg-secondary)] p-3">
      <div className="text-xs text-[var(--color-text-tertiary)]">{label}</div>
      <div className="mt-1 truncate text-sm font-medium text-[var(--color-text-primary)]">{value}</div>
    </div>
  );
}
