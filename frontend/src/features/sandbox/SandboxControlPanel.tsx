/**
 * SandboxControlPanel — 沙盒运行左侧控制表单。
 *
 * 负责测试用例选择、运行对象选择和测试输入，避免 SandboxPage 承担大段表单 JSX。
 */

import type { ReactNode } from 'react';
import { Bot, FlaskConical, Save, Send, Square, Trash2, Users } from 'lucide-react';
import { Button, Spinner } from '@/components/design';
import { FormField, SelectField, TextAreaField, TextField } from '../../components/form';
import type { Agent } from '../../types/agent';

export interface SandboxCase {
  id: string;
  name: string;
  description: string;
  payload: {
    mode: 'agent' | 'team';
    agentId: string;
    agentName: string;
    message: string;
    modelConfigId?: string;
  };
  createdAt: string;
  updatedAt: string;
}

interface SandboxControlPanelProps {
  modelSelector?: ReactNode;
  loading: boolean;
  mode: 'agent' | 'team';
  setMode: (mode: 'agent' | 'team') => void;
  agents: Agent[];
  selectedAgent: Agent | null;
  runtimeAgent: Agent | null;
  agentId?: string;
  setAgentId: (id: string) => void;
  cases: SandboxCase[];
  selectedCaseId: string;
  setSelectedCaseId: (id: string) => void;
  caseName: string;
  setCaseName: (name: string) => void;
  savingCase: boolean;
  message: string;
  setMessage: (message: string) => void;
  isStreaming: boolean;
  onSaveCase: () => void;
  onLoadCase: () => void;
  onDeleteCase: () => void;
  onRun: () => void;
  onAbort: () => void;
  onReset: () => void;
}

export function SandboxControlPanel({
  modelSelector,
  loading,
  mode,
  setMode,
  agents,
  selectedAgent,
  runtimeAgent,
  agentId,
  setAgentId,
  cases,
  selectedCaseId,
  setSelectedCaseId,
  caseName,
  setCaseName,
  savingCase,
  message,
  setMessage,
  isStreaming,
  onSaveCase,
  onLoadCase,
  onDeleteCase,
  onRun,
  onAbort,
  onReset,
}: SandboxControlPanelProps) {
  return (
    <aside className="min-h-0 overflow-y-auto rounded-md bg-[var(--color-bg-primary)] p-4 shadow-[0_12px_30px_rgba(0,0,0,0.08)]">
      <div className="mb-4 flex items-center gap-2">
        <FlaskConical size={18} color="var(--color-accent)" />
        <div>
          <h2 className="text-base font-medium text-[var(--color-text-primary)]">测试沙盒</h2>
          <div className="text-xs text-[var(--color-text-secondary)]">不写历史，仅用于临时验证</div>
        </div>
      </div>

      {loading ? (
        <div className="flex justify-center py-10"><Spinner /></div>
      ) : (
        <div className="space-y-4">
          <div>
            <SelectField
              label="测试用例"
              value={selectedCaseId || undefined}
              onChange={(value) => setSelectedCaseId(String(value))}
              options={cases.map((item) => ({ label: item.name, value: item.id }))}
              placeholder="选择已保存用例"
            />
            <div className="mt-2">
              <TextField
                value={caseName}
                onChange={(event) => setCaseName(event.target.value)}
                placeholder="用例名称"
              />
              <div className="mt-2 flex items-center gap-2">
                <Button size="small" disabled={!runtimeAgent || !message.trim()} loading={savingCase} onClick={onSaveCase}>
                  <Save size={13} />
                  保存
                </Button>
                <Button size="small" disabled={!selectedCaseId} onClick={onLoadCase}>
                  加载用例
                </Button>
                <Button size="small" disabled={!selectedCaseId} onClick={onDeleteCase} title="删除用例">
                  <Trash2 size={13} />
                </Button>
              </div>
            </div>
          </div>

          <FormField label="运行模式">
            <div className="grid grid-cols-2 gap-2">
              <Button type={mode === 'agent' ? 'primary' : 'default'} onClick={() => setMode('agent')}>
                <Bot size={14} />
                单 Agent
              </Button>
              <Button type={mode === 'team' ? 'primary' : 'default'} onClick={() => setMode('team')}>
                <Users size={14} />
                Team
              </Button>
            </div>
          </FormField>

          {mode === 'agent' && (
            <SelectField
              label="Agent"
              value={agentId || selectedAgent?.id}
              onChange={(value) => setAgentId(String(value))}
              options={agents.map((agent) => ({ label: agent.name, value: agent.id }))}
              placeholder="选择 Agent"
            />
          )}

          <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-3">
            <div className="text-sm font-medium text-[var(--color-text-primary)]">
              {runtimeAgent?.name || '未选择运行对象'}
            </div>
            <div className="mt-1 text-xs text-[var(--color-text-secondary)]">
              {modelSelector}
            </div>
            {mode === 'team' && runtimeAgent && (
              <div className="mt-2 text-xs text-[var(--color-text-tertiary)]">作为 Leader 动态组建临时成员</div>
            )}
          </div>

          <TextAreaField
            label="测试输入"
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            fieldClassName="h-48 resize-none"
            placeholder="输入一条测试任务"
          />

          <div className="flex gap-2">
            <Button type="primary" disabled={!runtimeAgent || !message.trim()} loading={isStreaming} onClick={onRun}>
              <Send size={14} />
              运行测试
            </Button>
            <Button disabled={!isStreaming} onClick={onAbort}>
              <Square size={14} />
              停止
            </Button>
            <Button disabled={isStreaming} onClick={onReset}>清空</Button>
          </div>
        </div>
      )}
    </aside>
  );
}
