/**
 * runTypes — Chat 后台运行任务类型。
 */

export interface RunSummary {
  id: string;
  sessionId: string;
  agentId: string;
  agentName: string;
  task: string;
  mode: string;
  status: string;
  createdAt: string;
  completedAt: string | null;
  eventCount: number;
  subscriberCount: number;
  latestEvent: string;
  progress: number;
  currentStage: string;
  activeActors: string[];
  resultPreview: string;
  resultLength: number;
  memberTotal: number;
  memberCompleted: number;
}

export interface RunDetail extends RunSummary {
  events: string[];
}
