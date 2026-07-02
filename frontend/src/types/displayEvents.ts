export const DISPLAY_BLOCKS = {
  PROCESS: 'process',
  TEAM: 'team',
  PLAN: 'plan',
  FEEDBACK: 'feedback',
  ANSWER: 'answer',
  FILES: 'files',
  ERROR: 'error',
} as const;

export const DISPLAY_EVENT_OPS = {
  APPEND: 'append',
  UPSERT: 'upsert',
  REPLACE: 'replace',
  REMOVE: 'remove',
  CLEAR: 'clear',
} as const;

export const DISPLAY_EVENTS = {
  REASONING_DELTA: 'reasoning.delta',
  THINKING_DELTA: 'thinking.delta',
  TOOL_STARTED: 'tool.started',
  TOOL_INPUT_DELTA: 'tool.input.delta',
  TOOL_OUTPUT_DELTA: 'tool.output.delta',
  TOOL_COMPLETED: 'tool.completed',
  TOOL_FAILED: 'tool.failed',
  SKILL_STARTED: 'skill.started',
  SKILL_UPDATED: 'skill.updated',
  SKILL_COMPLETED: 'skill.completed',
  SKILL_FAILED: 'skill.failed',
  TEAM_ACTORS_UPDATED: 'team.actors.updated',
  ANSWER_STARTED: 'answer.started',
  ANSWER_DELTA: 'answer.delta',
  ANSWER_COMPLETED: 'answer.completed',
  PLAN_WAITING: 'plan.waiting',
  PLAN_UPDATED: 'plan.updated',
  PLAN_COMPLETED: 'plan.completed',
  PLAN_FAILED: 'plan.failed',
  FEEDBACK_WAITING: 'feedback.waiting',
  FEEDBACK_RESOLVED: 'feedback.resolved',
  FILE_CREATED: 'file.created',
  FILE_UPDATED: 'file.updated',
  ERROR_RAISED: 'error.raised',
} as const;

export const DISPLAY_EVENT_SCOPES = {
  SINGLE_AGENT: 'single_agent',
  TEAM: 'team',
  LEADER: 'leader',
  WORKER: 'worker',
} as const;

export const DISPLAY_SNAPSHOT_VERSION = 3;

type ValueOf<T> = T[keyof T];

export type DisplayBlockType = ValueOf<typeof DISPLAY_BLOCKS>;
export type DisplayEventOp = ValueOf<typeof DISPLAY_EVENT_OPS>;
export type KnownDisplayEventName = ValueOf<typeof DISPLAY_EVENTS>;
export type DisplayEventName = KnownDisplayEventName | (string & {});
export type DisplayEventScope = ValueOf<typeof DISPLAY_EVENT_SCOPES> | (string & {});
export type DisplayStatus =
  | 'pending'
  | 'running'
  | 'waiting'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | string;

export type DisplayData = Record<string, unknown>;

export interface DisplayItem {
  id: string;
  event: DisplayEventName;
  targetId: string;
  status?: DisplayStatus;
  createdAt?: string;
  updatedAt: string;
  data: DisplayData;
}

export interface DisplayBlock {
  id: string;
  block: DisplayBlockType;
  status: DisplayStatus;
  updatedAt: string;
  items: DisplayItem[];
  data: DisplayData;
}

export type DisplayMode = 'single_agent' | 'team' | string;

export interface DisplayActor {
  id: string;
  role: 'single' | 'leader' | 'worker' | string;
  agentId: string;
  agentName: string;
  activityId?: string | null;
  status: DisplayStatus;
  task: string;
  assignment: string;
  expectedOutput: string;
  constraints: string;
  detail: string;
  output: string;
  report: string;
  skippedReason: string;
  reportReviewStatus: string;
  reportReviewNotes: string;
  reportReviewGaps: string[];
  executionMode: string;
  dependsOn: string[];
  waitReason: string;
  dependencyOutputCount: number;
  leaderContextCount: number;
  createdAt: string;
  updatedAt: string;
  completedAt: string;
  items: DisplayItem[];
  data: DisplayData;
}

export interface DisplaySnapshot {
  version: typeof DISPLAY_SNAPSHOT_VERSION;
  mode: DisplayMode;
  actors: DisplayActor[];
  blocks: DisplayBlock[];
}

export interface DisplayEvent {
  id: string;
  runId: string;
  sessionId: string;
  seq: number;
  createdAt: string;
  scope: DisplayEventScope;
  block: DisplayBlockType;
  event: DisplayEventName;
  op: DisplayEventOp;
  targetId?: string;
  actorId?: string;
  status?: DisplayStatus;
  data?: DisplayData;
}

export const DISPLAY_BLOCK_ORDER: DisplayBlockType[] = [
  DISPLAY_BLOCKS.TEAM,
  DISPLAY_BLOCKS.PROCESS,
  DISPLAY_BLOCKS.PLAN,
  DISPLAY_BLOCKS.FEEDBACK,
  DISPLAY_BLOCKS.ANSWER,
  DISPLAY_BLOCKS.FILES,
  DISPLAY_BLOCKS.ERROR,
];

export const DISPLAY_EVENT_NAMES: KnownDisplayEventName[] = Object.values(DISPLAY_EVENTS);
