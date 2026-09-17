/**
 * ScheduledJobContractPanel — 能力合同与到期工具预览。
 */

import { Tag } from '@/components/design';
import type { JobCapabilityContract, ScheduledJob } from '../../types/scheduledJob';

const VERDICT_LABELS: Record<JobCapabilityContract['verdict'], string> = {
  ok: '做得到',
  partial: '已降级',
  generate_only: '仅生成文件',
};

interface ScheduledJobContractPanelProps {
  job: ScheduledJob;
}

const NETWORK_PREVIEW_TOOLS = new Set(['HttpRequest', 'WebFetch', 'Bash']);

export function ScheduledJobContractPanel({ job }: ScheduledJobContractPanelProps) {
  const contract = job.capabilityContract;
  const gateOffPreview = contract.verdict === 'generate_only'
    && contract.claimed.length === 0
    && job.firingToolsPreview.some((name) => NETWORK_PREVIEW_TOOLS.has(name));
  return (
    <section className="rounded-xl bg-[var(--color-bg-primary)] p-4">
      <div className="mb-3 text-sm font-semibold text-[var(--color-text-primary)]">能力合同</div>
      <div className="mb-3 flex flex-wrap gap-1">
        <Tag size="small" color={contract.verdict === 'ok' ? 'green' : contract.verdict === 'partial' ? 'gold' : 'transparent'}>
          {gateOffPreview ? '门禁未开启' : VERDICT_LABELS[contract.verdict]}
        </Tag>
        {contract.acceptedPartial ? <Tag size="small" color="blue">用户同意降级</Tag> : null}
      </div>
      {contract.taskIntent ? (
        <div className="mb-3 text-sm text-[var(--color-text-secondary)]">
          原意：{contract.taskIntent}
        </div>
      ) : null}
      {contract.claimed.some((item) => item.layer === 'required') ? (
        <div className="mb-3">
          <div className="mb-1 text-xs text-[var(--color-text-tertiary)]">必注入（门禁不能裁）</div>
          <div className="flex flex-wrap gap-1">
            {contract.claimed.filter((item) => item.layer === 'required').map((item) => (
              <Tag key={item.capabilityId} size="small" color="blue">
                {item.toolName || item.capabilityId}
              </Tag>
            ))}
          </div>
        </div>
      ) : (
        <div className="mb-3 text-sm text-[var(--color-text-tertiary)]">
          {gateOffPreview
            ? '能力门禁未开启：到期按下面预览的 Agent 默认工具执行，空合同不会裁掉 HttpRequest / WebFetch。'
            : 'Phase 1 存量任务：到期只生成文件，不会自动带上后来绑定的 MCP。'}
        </div>
      )}
      {contract.claimed.some((item) => item.layer !== 'required') ? (
        <div className="mb-3">
          <div className="mb-1 text-xs text-[var(--color-text-tertiary)]">本条认领的可选工具</div>
          <div className="flex flex-wrap gap-1">
            {contract.claimed.filter((item) => item.layer !== 'required').map((item) => (
              <Tag key={item.capabilityId} size="small" color="transparent">
                {item.toolName || item.capabilityId} ({item.kind})
              </Tag>
            ))}
          </div>
        </div>
      ) : null}
      {contract.missing.length > 0 ? (
        <div className="mb-3 text-sm text-[var(--color-error)]">
          缺口：{contract.missing.map((item) => item.intent).join('、')}。不会按这些缺口去发信或调外部接口。
        </div>
      ) : null}
      <div className="mb-1 text-xs text-[var(--color-text-tertiary)]">到期将使用的工具</div>
      <div className="flex flex-wrap gap-1">
        {(job.firingToolsPreview.length > 0 ? job.firingToolsPreview : ['Read', 'Write', 'Edit']).map((name) => (
          <Tag key={name} size="small" color="green">{name}</Tag>
        ))}
      </div>
    </section>
  );
}
