/**
 * AgentGuardrailsSection — Agent 安全边界配置区块。
 *
 * 负责网络、执行隔离、人工审批、工具权限范围和工具超时策略。
 */

import { FormGrid, NumberField, SelectField, TextField, ToggleField } from '../../../components/form';
import type { AgentCreatePayload } from '../../../types/agent';

interface AgentGuardrailsSectionProps {
  form: AgentCreatePayload;
  onChange: (form: AgentCreatePayload) => void;
}

function legacyModeFromProfile(profile: string): 'ask_every_time' | 'accept_edits' | 'bypass' {
  if (profile === 'read_only' || profile === 'ask_every_time') return 'ask_every_time';
  if (profile === 'trusted_tools') return 'bypass';
  return 'accept_edits';
}

export function AgentGuardrailsSection({
  form,
  onChange,
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
              Docker 会把工具运行放入容器工作区，需要本机 Docker 可用。
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

        <div className="flex items-center justify-between gap-4 rounded-md border border-[var(--color-border)] bg-[var(--color-bg-primary)] px-3 py-2">
          <div>
            <div className="text-sm text-[var(--color-text-primary)]">强制人工审批</div>
            <div className="mt-1 text-xs text-[var(--color-text-tertiary)]">
              开启后会覆盖下方权限范围，工具调用按每次确认处理。
            </div>
          </div>
          <ToggleField
            checked={form.guardrails.humanApproval}
            onChange={(checked) => onChange({
              ...form,
              guardrails: { ...form.guardrails, humanApproval: checked },
            })}
            checkedLabel="开启"
            uncheckedLabel="关闭"
          />
        </div>

        <SelectField
          label="工具权限范围"
          value={form.guardrails.toolPermissionProfile}
          disabled={form.guardrails.humanApproval}
          onChange={(value) => onChange({
            ...form,
            guardrails: {
              ...form.guardrails,
              toolPermissionProfile: value as typeof form.guardrails.toolPermissionProfile,
              toolPermissionMode: legacyModeFromProfile(String(value)),
            },
          })}
          options={[
            { label: '只读工具 - 仅读取/搜索文件', value: 'read_only' },
            { label: '可信工具 - 自动放行', value: 'trusted_tools' },
            { label: '破坏性操作确认 - 推荐', value: 'confirm_destructive' },
            { label: '每次工具调用都确认', value: 'ask_every_time' },
          ]}
          help="只读模式会移除写入、Bash、HTTP 和 MCP；可信工具适合受控沙箱；破坏性确认适合日常开发。"
        />

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
