/**
 * TraceStatus — 运行状态标签。
 */

import { CheckCircle2, Clock3, XCircle } from 'lucide-react';
import { Tag } from '@/components/design';

export function TraceStatus({ status }: { status: string }) {
  const Icon = status === 'failed' ? XCircle : status === 'running' || status === 'waiting' ? Clock3 : CheckCircle2;
  return (
    <Tag
      size="small"
      color={status === 'failed' ? 'red' : status === 'waiting' ? 'orange' : 'green'}
      className="inline-flex items-center gap-1 whitespace-nowrap"
      icon={<Icon size={12} />}
    >
      {status}
    </Tag>
  );
}
