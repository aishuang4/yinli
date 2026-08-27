/**
 * AgentGuardrailsSection — Agent 安全边界配置区块。
 *
 * 负责网络、执行隔离、工具权限与审批、工具超时策略。
 */

import { FormGrid, NumberField, SelectField, TextField, ToggleField } from '../../../components/form';
import type { AgentCreatePayload } from '../../../types/agent';

interface AgentGuardrailsSectionProps {
  form: AgentCreatePayload;
  onChange: (form: AgentCreatePayload) => void;
  lockUnattendedPolicy?: boolean;
}

function legacyModeFromProfile(profile: string): 'ask_every_time' | 'accept_edits' | 'bypass' {
  if (profile === 'read_only' || profile === 'ask_every_time') return 'ask_every_time';
  if (profile === 'trusted_tools') return 'bypass';
  return 'accept_edits';
}

const FORCE_HUMAN_APPROVAL = 'force_human_approval';

function permissionStrategyValue(form: AgentCreatePayload): string {
  return form.guardrails.humanApproval
    ? FORCE_HUMAN_APPROVAL
    : form.guardrails.toolPermissionProfile;
}

function permissionStrategyHelp(form: AgentCreatePayload): string {
  if (form.guardrails.humanApproval) {
    return `当前会对所有可用工具逐次确认；保留的基础权限范围为“${permissionProfileLabel(form.guardrails.toolPermissionProfile)}”。`;
  }
  return '只读模式会移除写入、Bash、HTTP 和 MCP；可信工具适合受控沙箱；破坏性确认适合日常开发。';
}

function permissionProfileLabel(profile: string): string {
  return {
    read_only: '只读工具',
    trusted_tools: '可信工具',
    confirm_destructive: '破坏性操作确认',
    ask_every_time: '每次工具调用确认',
  }[profile] || '破坏性操作确认';
}

export function AgentGuardrailsSection({
  form,
  onChange,
  lockUnattendedPolicy = false,
}: AgentGuardrailsSectionProps) {
  return (
    <section className="rounded-xl bg-[var(--color-bg-primary)] p-4 shadow-sm">
      <div className="mb-4">
        <h3 className="text-sm font-medium text-[var(--color-text-primary)]">
          安全边界
        </h3>
        <div className="mt-1 text-xs text-[var(--color-text-tertiary)]">
          控制工具执行的网络、审批和超时策略。
        </div>
      </div>
      <div className="space-y-3">
        <div className="flex items-center justify-between gap-4 rounded-md border border-[var(--color-border)] bg-[var(--color-bg-primary)] px-3 py-2">
          <div className="text-sm text-[var(--color-text-primary)]">允许网络访问</div>
          <ToggleField
            checked={form.guardrails.networkEnabled}
            disabled={lockUnattendedPolicy}
            onChange={(checked) => onChange({
              ...form,
              guardrails: { ...form.guardrails, networkEnabled: checked },
            })}
            checkedLabel="开启"
            uncheckedLabel="关闭"
          />
        </div>

        <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-primary)] px-3 py-3">
          <div className="mb-2">
            <div className="text-sm text-[var(--color-text-primary)]">执行隔离</div>
            <div className="mt-1 text-xs text-[var(--color-text-tertiary)]">
              本地命令黑名单只能拦截明显误操作；需要安全隔离时请选择 Docker 沙盒。
            </div>
          </div>
          <SelectField
            value={form.guardrails.executionBackend}
            onChange={(value) => onChange({
              ...form,
              guardrails: {
                ...form.guardrails,
                executionBackend: value as 'local' | 'docker',
              },
            })}
            options={[
              { label: '本地工作区', value: 'local' },
              { label: 'Docker 沙盒', value: 'docker' },
            ]}
          />
          {form.guardrails.executionBackend === 'docker' && (
            <FormGrid className="mt-3" columns="auto">
              <TextField
                label="Docker 基础镜像"
                value={form.guardrails.dockerImage}
                onChange={(event) => onChange({
                  ...form,
                  guardrails: { ...form.guardrails, dockerImage: event.target.value },
                })}
                placeholder="python:3.11-slim"
              />
              <ToggleField
                label="沙盒持久化"
                checked={form.guardrails.persistentSandbox}
                onChange={(checked) => onChange({
                  ...form,
                  guardrails: { ...form.guardrails, persistentSandbox: checked },
                })}
                checkedLabel="持久"
                uncheckedLabel="临时"
              />
            </FormGrid>
          )}
        </div>

        <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-primary)] px-3 py-3">
          <SelectField
            label="工具权限与审批"
            value={permissionStrategyValue(form)}
            disabled={lockUnattendedPolicy}
            onChange={(value) => {
              const strategy = String(value);
              if (strategy === FORCE_HUMAN_APPROVAL) {
                onChange({
                  ...form,
                  guardrails: { ...form.guardrails, humanApproval: true },
                });
                return;
              }
              onChange({
                ...form,
                guardrails: {
                  ...form.guardrails,
                  humanApproval: false,
                  toolPermissionProfile: strategy as typeof form.guardrails.toolPermissionProfile,
                  toolPermissionMode: legacyModeFromProfile(strategy),
                },
              });
            }}
            options={[
              { label: '只读工具 - 仅读取/搜索文件', value: 'read_only' },
              { label: '可信工具 - 自动放行', value: 'trusted_tools' },
              { label: '破坏性操作确认 - 推荐', value: 'confirm_destructive' },
              { label: '每次工具调用都确认', value: 'ask_every_time' },
              { label: '强制人工审批 - 所有可用工具均需确认', value: FORCE_HUMAN_APPROVAL },
            ]}
            help={lockUnattendedPolicy
              ? '系统定时任务 Agent 固定开启网络，并使用可信工具无人值守执行。'
              : permissionStrategyHelp(form)}
          />
        </div>

        <NumberField
          label="工具调用超时（秒）"
          min={10}
          max={3600}
          value={form.guardrails.timeoutSeconds}
          onChange={(value) => onChange({
            ...form,
            guardrails: { ...form.guardrails, timeoutSeconds: Number(value ?? 10) },
          })}
          style={{ width: 128 }}
        />
      </div>
    </section>
  );
}
