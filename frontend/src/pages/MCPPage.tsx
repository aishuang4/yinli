/**
 * MCPPage — MCP 浏览器页面
 *
 * 支持浏览 MCP Server、按分类/安装状态筛选、安装/卸载、启停和查看工具详情。
 */

import { useMemo, useState } from 'react';
import { CheckCircle2, Plus, Plug, RefreshCw, Search, Trash2, Wrench } from 'lucide-react';
import {
  Button,
  Card,
  Empty,
  Input,
  Modal,
  Select,
  Spinner,
  Tag,
} from '@agentscope-ai/design';
import { ToggleButton } from '../components/ToggleButton';
import { useMcp } from '../hooks/useMcp';
import type { McpServer } from '../types/mcp';

type InstallFilter = 'all' | 'installed' | 'available';

const installFilterOptions = [
  { label: '全部', value: 'all' },
  { label: '已安装', value: 'installed' },
  { label: '可安装', value: 'available' },
];

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

export function MCPPage() {
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
  const [installFilter, setInstallFilter] = useState<InstallFilter>('all');
  const [selectedServer, setSelectedServer] = useState<McpServer | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [deleteConfirm, setDeleteConfirm] = useState<McpServer | null>(null);
  const [registerOpen, setRegisterOpen] = useState(false);
  const [registering, setRegistering] = useState(false);
  const [registerError, setRegisterError] = useState<string | null>(null);
  const [registerForm, setRegisterForm] = useState({
    name: '',
    url: '',
    description: '',
    headers: [{ key: '', value: '' }],
  });

  const filteredServers = useMemo(() => (
    servers.filter((server) => {
      if (!serverMatchesQuery(server, query)) return false;
      if (category !== 'all' && server.category !== category) return false;
      if (installFilter === 'installed') return server.status === 'installed';
      if (installFilter === 'available') return server.status !== 'installed';
      return true;
    })
  ), [servers, query, category, installFilter]);

  const installedCount = servers.filter((server) => server.status === 'installed').length;
  const enabledToolCount = servers
    .filter((server) => server.status === 'installed' && server.enabled)
    .reduce((total, server) => total + server.tools.length, 0);

  const categoryOptions = [
    { label: '全部分类', value: 'all' },
    ...categories.map((item) => ({ label: item, value: item })),
  ];

  async function runServerAction(
    serverId: string,
    action: () => Promise<McpServer>,
  ) {
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

  return (
    <div className="h-full grid grid-rows-[auto_auto_1fr] min-h-0 bg-[var(--color-bg-primary)]">
      <header className="px-5 py-4 border-b border-[var(--color-border)]">
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2">
              <Plug size={20} color="var(--color-accent)" />
              <h1 className="text-lg font-semibold text-[var(--color-text-primary)]">
                MCP Browser
              </h1>
            </div>
            <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
              浏览、安装和管理 Agent 可用的 MCP Server 工具。
            </p>
          </div>
          <div className="flex items-center gap-3 text-sm text-[var(--color-text-secondary)]">
            <span>{servers.length} 个 Server</span>
            <span>{installedCount} 个已安装</span>
            <span>{enabledToolCount} 个可用工具</span>
            <Button
              type="primary"
              onClick={() => {
                setRegisterError(null);
                setRegisterOpen(true);
              }}
            >
              <span className="inline-flex items-center gap-1.5">
                <Plus size={14} className="shrink-0" />
                注册 MCP
              </span>
            </Button>
            <Button
              icon={<RefreshCw size={14} />}
              onClick={refresh}
              loading={loading}
            >
              刷新
            </Button>
          </div>
        </div>
      </header>

      <section className="px-5 py-3 border-b border-[var(--color-border)]">
        <div className="grid grid-cols-[minmax(240px,1fr)_180px_160px] gap-3">
          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索 Server、工具或标签"
            prefix={<Search size={14} />}
            allowClear
          />
          <Select
            value={category}
            onChange={setCategory}
            options={categoryOptions}
          />
          <Select
            value={installFilter}
            onChange={setInstallFilter}
            options={installFilterOptions}
          />
        </div>
      </section>

      <main className="min-h-0 overflow-y-auto p-5">
        {error ? (
          <div className="rounded-md border border-[var(--color-error)] bg-[var(--color-error-bg)] p-4 text-sm text-[var(--color-error)]">
            {error}
          </div>
        ) : loading ? (
          <div className="flex justify-center py-12">
            <Spinner tip="加载 MCP Server..." />
          </div>
        ) : filteredServers.length === 0 ? (
          <Empty type="noData" description="没有匹配的 MCP Server" />
        ) : (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
            {filteredServers.map((server) => {
              const installed = server.status === 'installed';
              const busy = busyId === server.id;

              return (
                <Card
                  key={server.id}
                  className="h-[400px] overflow-hidden transition-all duration-300 ease-out hover:-translate-y-1 hover:shadow-lg"
                  bodyStyle={{ height: '100%', padding: 0 }}
                >
                  <div className="grid h-full grid-rows-[minmax(0,62px)_minmax(0,58px)_minmax(0,32px)_minmax(0,66px)_minmax(0,34px)_minmax(0,1fr)_minmax(0,62px)] overflow-hidden">
                    <div className="flex min-h-0 items-start justify-between gap-3 border-b border-[var(--color-border)] px-4 py-3">
                      <h2 className="line-clamp-2 min-w-0 text-sm font-semibold text-[var(--color-text-primary)]">
                        {server.name}
                      </h2>
                      <div className="shrink-0">
                        <Tag color={installed ? 'success' : 'info'} size="small">
                          {installed ? '已安装' : '可安装'}
                        </Tag>
                      </div>
                    </div>

                    <div className="min-h-0 overflow-hidden px-4 py-2">
                      <p className="line-clamp-2 text-xs text-[var(--color-text-secondary)]">
                        {server.description}
                      </p>
                    </div>

                    <div className="min-h-0 overflow-hidden px-4">
                      <div className="flex h-7 flex-nowrap gap-2 overflow-hidden">
                        <Tag size="small" color="blue">{server.category}</Tag>
                        <Tag size="small" color="transparent">{server.transport}</Tag>
                        {server.tags.slice(0, 2).map((tag) => (
                          <Tag key={tag} size="small" color="mauve">{tag}</Tag>
                        ))}
                      </div>
                    </div>

                    <div className="grid min-h-0 grid-cols-2 gap-2 px-4 text-sm">
                      <div className="min-w-0 overflow-hidden rounded-md bg-[var(--color-bg-secondary)] px-3 py-2">
                        <div className="text-xs text-[var(--color-text-tertiary)]">工具数</div>
                        <div className="mt-1 font-medium text-[var(--color-text-primary)]">
                          {server.tools.length}
                        </div>
                      </div>
                      <div className="min-w-0 overflow-hidden rounded-md bg-[var(--color-bg-secondary)] px-3 py-2">
                        <div className="text-xs text-[var(--color-text-tertiary)]">状态</div>
                        <div className="mt-1 flex min-w-0 items-center gap-1 truncate font-medium text-[var(--color-text-primary)]">
                          {installed && server.enabled && <CheckCircle2 size={14} color="var(--color-success)" />}
                          {installed ? (server.enabled ? '已启用' : '已停用') : '未安装'}
                        </div>
                      </div>
                    </div>

                    <div className="line-clamp-1 min-h-0 overflow-hidden px-4 py-1 text-xs text-[var(--color-text-secondary)]">
                      {server.tools.map((tool) => tool.name).join(' / ')}
                    </div>

                    <div aria-hidden="true" />

                    <div className="flex min-h-0 items-center justify-between gap-3 self-end px-4 py-3">
                      {installed ? (
                        <ToggleButton
                          checked={server.enabled}
                          checkedLabel="启用中"
                          uncheckedLabel="已停用"
                          disabled={busy}
                          ariaLabel={`${server.name} 启停状态`}
                          onChange={(enabled) => runServerAction(
                            server.id,
                            () => setServerEnabled(server.id, enabled),
                          )}
                        />
                      ) : (
                        <span className="text-xs text-[var(--color-text-tertiary)]">
                          安装后可在 Agent 中选择工具
                        </span>
                      )}

                      <div className="flex shrink-0 gap-2">
                        <Button onClick={() => setSelectedServer(server)}>
                          详情
                        </Button>
                        {installed ? (
                          <Button
                            danger
                            loading={busy}
                            onClick={() => runServerAction(
                              server.id,
                              () => uninstallServer(server.id),
                            )}
                          >
                            卸载
                          </Button>
                        ) : (
                          <Button
                            type="primary"
                            loading={busy}
                            onClick={() => runServerAction(
                              server.id,
                              () => installServer(server.id),
                            )}
                          >
                            安装
                          </Button>
                        )}
                        <Button
                          type="text"
                          danger
                          loading={busy}
                          onClick={() => setDeleteConfirm(server)}
                          aria-label="删除 MCP"
                        >
                          <Trash2 size={14} />
                        </Button>
                      </div>
                    </div>
                  </div>
                </Card>
              );
            })}
          </div>
        )}
      </main>

      <Modal
        title={selectedServer?.name}
        open={Boolean(selectedServer)}
        onCancel={() => setSelectedServer(null)}
        footer={null}
        width={720}
      >
        {selectedServer && (
          <div className="space-y-5">
            <p className="text-sm text-[var(--color-text-secondary)]">
              {selectedServer.description}
            </p>

            <div className="grid grid-cols-2 gap-3 text-sm">
              <div>
                <div className="text-xs text-[var(--color-text-tertiary)]">分类</div>
                <div className="mt-1 text-[var(--color-text-primary)]">{selectedServer.category}</div>
              </div>
              <div>
                <div className="text-xs text-[var(--color-text-tertiary)]">通信方式</div>
                <div className="mt-1 text-[var(--color-text-primary)]">{selectedServer.transport}</div>
              </div>
              <div className="col-span-2">
                <div className="text-xs text-[var(--color-text-tertiary)]">启动命令</div>
                <code className="mt-1 block rounded-md bg-[var(--color-bg-secondary)] px-3 py-2 text-xs text-[var(--color-text-primary)]">
                  {selectedServer.command || selectedServer.url || '未配置'}
                </code>
              </div>
              {Object.keys(selectedServer.headers).length > 0 && (
                <div className="col-span-2">
                  <div className="text-xs text-[var(--color-text-tertiary)]">请求头</div>
                  <div className="mt-1 space-y-1 rounded-md bg-[var(--color-bg-secondary)] px-3 py-2 text-xs text-[var(--color-text-primary)]">
                    {Object.keys(selectedServer.headers).map((key) => (
                      <div key={key}>{key}: ********</div>
                    ))}
                  </div>
                </div>
              )}
            </div>

            <section>
              <div className="mb-3 flex items-center gap-2 text-sm font-medium text-[var(--color-text-primary)]">
                <Wrench size={16} />
                提供的工具
              </div>
              <div className="space-y-2">
                {selectedServer.tools.map((tool) => (
                  <div
                    key={tool.id}
                    className="rounded-md border border-[var(--color-border)] p-3"
                  >
                    <div className="font-medium text-[var(--color-text-primary)]">{tool.name}</div>
                    <div className="mt-1 text-sm text-[var(--color-text-secondary)]">
                      {tool.description}
                    </div>
                    <code className="mt-2 block text-xs text-[var(--color-text-tertiary)]">
                      {tool.id}
                    </code>
                  </div>
                ))}
              </div>
            </section>
          </div>
        )}
      </Modal>

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

      <Modal
        title="注册 MCP Server"
        open={registerOpen}
        onCancel={() => setRegisterOpen(false)}
        onOk={handleRegister}
        confirmLoading={registering}
        okText="拉取并注册"
        cancelText="取消"
        width={560}
      >
        <div className="space-y-4">
          {registerError && (
            <div className="rounded-md border border-[var(--color-error)] bg-[var(--color-error-bg)] p-3 text-sm text-[var(--color-error)]">
              {registerError}
            </div>
          )}

          <div>
            <label className="mb-1 block text-xs text-[var(--color-text-secondary)]">
              名称 <span className="text-[var(--color-error)]">*</span>
            </label>
            <Input
              value={registerForm.name}
              onChange={(event) => setRegisterForm({
                ...registerForm,
                name: event.target.value,
              })}
              placeholder="如：Company Docs MCP"
              maxLength={80}
            />
          </div>

          <div>
            <label className="mb-1 block text-xs text-[var(--color-text-secondary)]">
              MCP 地址 <span className="text-[var(--color-error)]">*</span>
            </label>
            <Input
              value={registerForm.url}
              onChange={(event) => setRegisterForm({
                ...registerForm,
                url: event.target.value,
              })}
              placeholder="https://example.com/mcp"
            />
            <div className="mt-1 text-xs text-[var(--color-text-tertiary)]">
              支持 Streamable HTTP / JSON-RPC 的 tools/list，也兼容返回 <code>{'{"tools": [...]}'}</code> 的简单接口。
            </div>
          </div>

          <div>
            <label className="mb-1 block text-xs text-[var(--color-text-secondary)]">
              描述
            </label>
            <Input.TextArea
              value={registerForm.description}
              onChange={(event) => setRegisterForm({
                ...registerForm,
                description: event.target.value,
              })}
              placeholder="说明这个 MCP Server 提供什么能力"
              rows={3}
              maxLength={300}
            />
          </div>

          <div>
            <div className="mb-2 flex items-center justify-between gap-3">
              <label className="block text-xs text-[var(--color-text-secondary)]">
                请求头
              </label>
              <Button
                size="small"
                onClick={() => setRegisterForm({
                  ...registerForm,
                  headers: [...registerForm.headers, { key: '', value: '' }],
                })}
              >
                <span className="inline-flex items-center gap-1">
                  <Plus size={12} className="shrink-0" />
                  添加
                </span>
              </Button>
            </div>
            <div className="space-y-2">
              {registerForm.headers.map((header, index) => (
                <div key={index} className="grid grid-cols-[1fr_1fr_32px] gap-2">
                  <Input
                    value={header.key}
                    onChange={(event) => {
                      const next = [...registerForm.headers];
                      next[index] = { ...next[index], key: event.target.value };
                      setRegisterForm({ ...registerForm, headers: next });
                    }}
                    placeholder="Header 名称，如 Authorization"
                  />
                  <Input.Password
                    value={header.value}
                    onChange={(event) => {
                      const next = [...registerForm.headers];
                      next[index] = { ...next[index], value: event.target.value };
                      setRegisterForm({ ...registerForm, headers: next });
                    }}
                    placeholder="Header 值"
                  />
                  <Button
                    type="text"
                    danger
                    disabled={registerForm.headers.length === 1}
                    onClick={() => setRegisterForm({
                      ...registerForm,
                      headers: registerForm.headers.filter((_, itemIndex) => itemIndex !== index),
                    })}
                    aria-label="删除请求头"
                  >
                    <Trash2 size={14} className="shrink-0" />
                  </Button>
                </div>
              ))}
            </div>
            <div className="mt-1 text-xs text-[var(--color-text-tertiary)]">
              可用于 Authorization、X-API-Key 等认证信息；Content-Type 和 Accept 由系统自动设置。
            </div>
          </div>
        </div>
      </Modal>
    </div>
  );
}
