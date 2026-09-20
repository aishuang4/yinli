/**
 * TraceStatus — 与 Chat Run 生命周期一致的状态标签。
 */

import { Ban, CheckCircle2, Clock3, LoaderCircle, XCircle } from 'lucide-react';
import { Tag } from '@/components/design';

export function TraceStatus({ status }: { status: string }) {
  const waiting = status.startsWith('waiting');
  const Icon = status === 'failed'
    ? XCircle
    : status === 'cancelled'
      ? Ban
      : status === 'running'
        ? LoaderCircle
        : waiting
          ? Clock3
          : CheckCircle2;
  const color = status === 'failed'
    ? 'red'
    : status === 'cancelled'
      ? 'transparent'
      : waiting
        ? 'orange'
        : status === 'running'
          ? 'blue'
          : 'green';
  const label = status === 'waiting_user'
    ? '等待用户'
    : status === 'waiting_permission'
      ? '等待权限'
      : status === 'waiting_external'
        ? '等待外部'
        : status === 'cancelled'
          ? '已取消'
          : status === 'failed'
            ? '失败'
            : status === 'running'
              ? '进行中'
              : status === 'completed'
                ? '已完成'
                : status;

  return (
    <Tag
      size="small"
      color={color}
      className="inline-flex items-center gap-1 whitespace-nowrap"
      icon={<Icon size={12} className={status === 'running' ? 'animate-spin' : undefined} />}
      style={{ marginInlineEnd: 0 }}
    >
      {label}
    </Tag>
  );
}
