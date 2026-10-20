/**
 * AgentGuardrailsSection — Agent 安全边界配置区块。
 *
 * 负责网络、执行隔离、平台能力范围和工具超时。
 */

import { FormGrid, NumberField, SelectField, TextField, ToggleField } from '../../../components/form';
import { useAuthStore } from '../../auth/authStore';
import type { AgentCreatePayload } from '../../../types/agent';

interface AgentGuardrailsSectionProps {
  form: AgentCreatePayload;
  onChange: (form: AgentCreatePayload) => void;
  lockUnattendedPolicy?: boolean;
  forceSandbox?: boolean;
}

export function AgentGuardrailsSection({
  form,
  onChange,
  lockUnattendedPolicy = false,
  forceSandbox = false,
}: AgentGuardrailsSectionProps) {
  const storedLocalExecution = useAuthStore((state) => state.capabilities.localExecution);
  const localExecution = forceSandbox ? false : storedLocalExecution;
  const sandboxImages = useAuthStore((state) => state.capabilities.sandboxImages);
  return (
    <section className="rounded-xl bg-[var(--color-bg-primary)] p-4 shadow-sm">
      <div className="mb-4">
        <h3 className="text-sm font-medium text-[var(--color-text-primary)]">
          安全边界
        </h3>
        <div className="mt-1 text-xs text-[var(--color-text-tertiary)]">
          控制文件、网络、执行隔离和只读能力；审核模式在 Chat 选择。
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
              {localExecution
                ? '本地命令黑名单只能拦截明显误操作；需要安全隔离时请选择 Docker 沙盒。'
                : 'Web 端命令在隔离沙箱中执行'}
            </div>
          </div>
          {localExecution ? (
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
          ) : (
            <SelectField
              label="沙箱镜像"
              value={sandboxImages.includes(form.guardrails.dockerImage) ? form.guardrails.dockerImage : (sandboxImages[0] || '')}
              onChange={(value) => onChange({
                ...form,
                guardrails: { ...form.guardrails, dockerImage: String(value), executionBackend: 'docker' },
              })}
              options={sandboxImages.map((image) => ({ label: image, value: image }))}
            />
          )}
          {localExecution && form.guardrails.executionBackend === 'docker' && (
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
            label="操作范围"
            value={form.guardrails.operationScope || 'normal'}
            onChange={value => onChange({
              ...form,
              guardrails: { ...form.guardrails, operationScope: value as 'normal' | 'read_only' },
            })}
            options={[
              { label: '正常能力范围', value: 'normal' },
              { label: '只读能力 - 仅读取/搜索文件', value: 'read_only' },
            ]}
            help="审核模式在 Chat 发送区选择。这里的能力限制不会被人工批准或完全开放扩大。"
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
