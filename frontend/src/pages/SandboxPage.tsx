import { useCallback, useEffect, useMemo, useState } from 'react';
import { useAppStore } from '../stores/appStore';
import { FlaskConical, Loader2 } from 'lucide-react';
import { Tag } from '@/components/design';
import { SandboxControlPanel, type SandboxCase } from '../features/sandbox/SandboxControlPanel';
import { useAgents } from '../hooks/useAgents';
import { useEventStream } from '../hooks/useEventStream';
import { RuntimeBlockTree } from '../features/runtime-stream/RuntimeBlockTree';
import { createId } from '../utils/id';
import { getErrorMessage, notifyError, notifySuccess } from '../utils/feedback';

import { useModelCatalog } from '../features/models/useModelCatalog';
import { ModelSelector } from '../features/models/ModelSelector';

export function SandboxPage() {
  const { catalog, loading: modelsLoading, error: modelsError } = useModelCatalog();
  const [modelChoice, setModelChoice] = useState('');
  const modelConfigId = modelChoice || catalog?.defaultModelId || '';
  const currentView = useAppStore((state) => state.currentView);
  const { agents, loading: agentsLoading, refresh: refreshAgents } = useAgents();
  const { state, sendMessage, abort, reset } = useEventStream();
  const [mode, setMode] = useState<'agent' | 'team'>('agent');
  const [agentId, setAgentId] = useState('');
  const [message, setMessage] = useState('请用一句话说明你会如何完成这个任务，并在需要时调用合适工具。');
  const [cases, setCases] = useState<SandboxCase[]>([]);
  const [selectedCaseId, setSelectedCaseId] = useState('');
  const [caseName, setCaseName] = useState('');
  const [savingCase, setSavingCase] = useState(false);

  const chatAgents = useMemo(() => agents.filter((agent) => agent.role !== 'job'), [agents]);
  const selectedAgent = useMemo(
    () => chatAgents.find((agent) => agent.id === agentId) || chatAgents[0] || null,
    [agentId, chatAgents]
  );
  const runtimeAgent = selectedAgent;

  useEffect(() => {
    if (currentView !== 'sandbox') return;
    void refreshAgents();
  }, [currentView, refreshAgents]);

  const loadCases = useCallback(async () => {
    const response = await fetch('/api/sandbox/cases');
    if (!response.ok) return;
    setCases(await response.json());
  }, []);

  useEffect(() => {
    void loadCases();
  }, [loadCases]);

  const saveCurrentCase = async () => {
    if (!runtimeAgent || !message.trim() || savingCase) return;
    const name = caseName.trim() || message.trim().slice(0, 40);
    setSavingCase(true);
    try {
      const response = await fetch('/api/sandbox/cases', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name,
          description: '',
          payload: {
            mode,
            agentId: runtimeAgent.id,
            agentName: runtimeAgent.name,
            message,
            modelConfigId,
          },
        }),
      });
      if (response.ok) {
        const nextCase: SandboxCase = await response.json();
        setCases((current) => [nextCase, ...current.filter((item) => item.id !== nextCase.id)]);
        setSelectedCaseId(nextCase.id);
        setCaseName('');
        notifySuccess('测试用例保存成功');
      } else {
        throw new Error(`HTTP ${response.status}`);
      }
    } catch (err) {
      notifyError(`测试用例保存失败：${getErrorMessage(err, '未知错误')}`);
    } finally {
      setSavingCase(false);
    }
  };

  const loadSelectedCase = () => {
    const item = cases.find((sandboxCase) => sandboxCase.id === selectedCaseId);
    if (!item) return;
    setMode(item.payload.mode);
    setAgentId(item.payload.agentId);
    setMessage(item.payload.message);
    setModelChoice(item.payload.modelConfigId || '');
    setCaseName(item.name);
    reset();
  };

  const deleteSelectedCase = async () => {
    if (!selectedCaseId) return;
    try {
      const response = await fetch(`/api/sandbox/cases/${selectedCaseId}`, { method: 'DELETE' });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      setCases((current) => current.filter((item) => item.id !== selectedCaseId));
      setSelectedCaseId('');
      notifySuccess('测试用例删除成功');
    } catch (err) {
      notifyError(`测试用例删除失败：${getErrorMessage(err, '未知错误')}`);
    }
  };

  const runSandbox = async () => {
    if (!message.trim() || !runtimeAgent || state.isStreaming) return;
    reset();
    await sendMessage(message.trim(), {
      sessionId: createId('sandbox'),
      agentId: runtimeAgent.id,
      agentName: runtimeAgent.name,
      mode,
      modelConfigId,
      skillIds: runtimeAgent.skills,
      sandbox: true,
      images: [],
    });
  };

  const loading = agentsLoading;

  return (
    <div className="grid h-full min-h-0 grid-cols-[360px_1fr] gap-4 overflow-hidden p-4">
      <SandboxControlPanel
        modelSelector={<ModelSelector catalog={catalog} value={modelConfigId} onChange={setModelChoice}
          disabled={state.isStreaming} loading={modelsLoading} error={modelsError} />}
        loading={loading}
        mode={mode}
        setMode={setMode}
        agents={chatAgents}
        selectedAgent={selectedAgent}
        runtimeAgent={runtimeAgent}
        agentId={agentId}
        setAgentId={setAgentId}
        cases={cases}
        selectedCaseId={selectedCaseId}
        setSelectedCaseId={setSelectedCaseId}
        caseName={caseName}
        setCaseName={setCaseName}
        savingCase={savingCase}
        message={message}
        setMessage={setMessage}
        isStreaming={state.isStreaming}
        onSaveCase={() => void saveCurrentCase()}
        onLoadCase={loadSelectedCase}
        onDeleteCase={() => void deleteSelectedCase()}
        onRun={() => void runSandbox()}
        onAbort={abort}
        onReset={reset}
      />

      <main className="min-h-0 overflow-y-auto rounded-md bg-[var(--color-bg-primary)] p-5 shadow-[0_12px_30px_rgba(0,0,0,0.08)]">
        <SandboxResult state={state} />
      </main>
    </div>
  );
}

function SandboxResult({ state }: { state: ReturnType<typeof useEventStream>['state'] }) {
  const snapshot = state.runtime.snapshot;
  if (!state.isStreaming && snapshot.order.length === 0 && !state.error) {
    return (
      <div className="flex h-full items-center justify-center text-[var(--color-text-tertiary)]">
        <div className="text-center">
          <FlaskConical size={46} strokeWidth={1.2} className="mx-auto mb-3" />
          <div className="text-sm">运行一次测试后查看结果和轨迹</div>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        {state.isStreaming && <Loader2 size={15} className="animate-spin text-[var(--color-accent)]" />}
        <h2 className="text-base font-medium text-[var(--color-text-primary)]">沙盒运行结果</h2>
        <Tag size="small" color="orange">Sandbox</Tag>
      </div>

      {state.error && (
        <div className="rounded-md border border-[var(--color-border)] p-3 text-sm text-[var(--color-error)]">{state.error}</div>
      )}
      <RuntimeBlockTree snapshot={snapshot} />
    </div>
  );
}
