/** 定时任务前端类型，字段与 /api/scheduled-jobs camelCase 契约对齐。 */

export const SCHEDULER_DISABLED_DETAIL = '调度未启用';

export type JobStatus = 'pending_confirmation' | 'active' | 'paused' | 'archived' | 'completed';
export type ScheduleKind = 'weekly' | 'once';
export type MissedPolicy = 'catch_up_once' | 'mark_missed' | 'skip_silent';
export type FailurePolicy = 'notify_keep_schedule' | 'pause_job';
export type DeliveryPolicy = 'none' | 'notify' | 'confirm_then_send' | 'auto_send';
export type FiringTrigger = 'schedule' | 'manual' | 'catch_up';
export type FiringStatus =
  | 'queued'
  | 'starting'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'cancelled'
  | 'missed'
  | 'skipped';

export interface JobToolScope {
  allowBash: boolean;
  allowHttp: boolean;
  allowWeb: boolean;
  allowMcp: boolean;
  allowKnowledge: boolean;
}

export interface WeeklyScheduleExpr {
  weekdays: string[];
  at: string;
}

export interface OnceScheduleExpr {
  at: string;
}

export type JobScheduleExpr = WeeklyScheduleExpr | OnceScheduleExpr | Record<string, unknown>;

export interface JobCapabilityClaim {
  capabilityId: string;
  kind: string;
  resourceId: string;
  serverId?: string;
  toolName: string;
  tags: string[];
  unattendedOk: boolean;
}

export interface JobCapabilityGap {
  intent: string;
  reason: string;
  suggestedAction?: string;
}

export interface JobCapabilityContract {
  version: number;
  verdict: 'ok' | 'partial' | 'generate_only';
  taskIntent: string;
  requiredIntents: string[];
  claimed: JobCapabilityClaim[];
  missing: JobCapabilityGap[];
  blockedByPermission: JobCapabilityGap[];
  acceptedPartial: boolean;
  allowHttpRequest: boolean;
  allowWebFetch: boolean;
  allowBash: boolean;
  skillIds: string[];
  assembledToolScope?: JobToolScope;
  evaluatedAt?: string;
  confirmedAt?: string | null;
}

export interface ScheduledJob {
  id: string;
  workspaceId: string;
  projectId: string;
  name: string;
  summary: string;
  status: JobStatus;
  pauseReason: string;
  agentId: string;
  mode: 'agent';
  scheduleKind: ScheduleKind;
  scheduleExpr: JobScheduleExpr;
  timezone: string;
  nextRunAt: string | null;
  lastRunAt: string | null;
  frozenPrompt: string;
  skillIds: string[];
  toolScope: JobToolScope;
  inputSpec: Record<string, unknown>;
  outputSpec: Record<string, unknown>;
  missedPolicy: MissedPolicy;
  failurePolicy: FailurePolicy;
  deliveryPolicy: DeliveryPolicy;
  deliveryChannelRef: string;
  recipientIds: string[];
  unattendedPermission: string;
  autoSendEnabledAt: string | null;
  requiredSuccesses: number;
  consecutiveSuccesses: number;
  consecutiveFailures: number;
  maxConsecutiveFailures: number;
  maxFiringsPerDay: number;
  createdFromSessionId: string;
  capabilityContract: JobCapabilityContract;
  firingToolsPreview: string[];
  createdAt: string;
  updatedAt: string;
  archivedAt: string | null;
}

export interface JobOutputFile {
  relativePath?: string;
  path?: string;
  sha256?: string;
  bytes?: number;
  size?: number;
}

export interface JobOutputManifest {
  files?: JobOutputFile[];
  [key: string]: unknown;
}

export interface ScheduledJobRun {
  id: string;
  jobId: string;
  workspaceId: string;
  projectId: string;
  trigger: FiringTrigger;
  scheduledFor: string;
  status: FiringStatus;
  chatSessionId: string;
  chatRunId: string;
  errorCode: string;
  errorMessage: string;
  outputManifest: JobOutputManifest;
  deliveryStatus: string;
  startedAt: string | null;
  completedAt: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface ScheduledJobSchedulePatch {
  scheduleKind?: ScheduleKind;
  scheduleExpr?: JobScheduleExpr;
  timezone?: string;
}
