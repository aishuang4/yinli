import type { DisplaySnapshot, DisplayStatus } from '../../types/displayEvents';
import type {
  ChatRunPlanStep,
  ClarificationRequestInfo,
  HitlRequestInfo,
  SkillCallInfo,
  ToolCallInfo,
} from '../../hooks/useEventStream';
import type { GeneratedFileInfo } from './generatedFiles';

export type ChatDisplayStatus = DisplayStatus;

export interface ChatDisplayMessage {
  id: string;
  sessionId: string;
  role: 'assistant';
  status: ChatDisplayStatus;
  isStreaming: boolean;
  startedAt: string;
  completedAt: string;
  snapshot: DisplaySnapshot;
  blocks: ChatDisplayBlock[];
  finalAnswer: string;
  modelName: string;
  tokenUsage: { input: number; output: number } | null;
}

export type ChatDisplayBlock =
  | ChatProcessBlock
  | ChatPlanBlock
  | ChatHumanFeedbackBlock
  | ChatFinalAnswerBlock
  | ChatGeneratedFilesBlock
  | ChatErrorBlock;

export type ChatProcessItem =
  | {
    id: string;
    type: 'reasoning';
    content: string;
    status: ChatDisplayStatus;
    updatedAt: string;
  }
  | {
    id: string;
    type: 'thinking';
    content: string;
    status: ChatDisplayStatus;
    updatedAt: string;
  }
  | {
    id: string;
    type: 'tool';
    toolCall: ToolCallInfo;
    status: ChatDisplayStatus;
    updatedAt: string;
  }
  | {
    id: string;
    type: 'skill';
    skillCall: SkillCallInfo;
    status: ChatDisplayStatus;
    updatedAt: string;
  };

export interface ChatProcessBlock {
  id: string;
  type: 'process';
  status: ChatDisplayStatus;
  startedAt: string;
  completedAt: string;
  updatedAt: string;
  items: ChatProcessItem[];
}

export interface ChatPlanBlock {
  id: string;
  type: 'plan';
  status: ChatDisplayStatus;
  planId: string;
  planFile: string;
  markdown: string;
  steps: ChatRunPlanStep[];
  updatedAt: string;
}

export interface ChatHumanFeedbackBlock {
  id: string;
  type: 'human_feedback';
  status: ChatDisplayStatus;
  hitlRequests: HitlRequestInfo[];
  clarificationRequests: ClarificationRequestInfo[];
  readOnlyClarification: boolean;
}

export interface ChatFinalAnswerBlock {
  id: string;
  type: 'final_answer';
  status: ChatDisplayStatus;
  content: string;
  started: boolean;
}

export interface ChatGeneratedFilesBlock {
  id: string;
  type: 'generated_files';
  status: ChatDisplayStatus;
  files: GeneratedFileInfo[];
}

export interface ChatErrorBlock {
  id: string;
  type: 'error';
  message: string;
}

export interface ChatDisplayMessageInput {
  displaySnapshot?: DisplaySnapshot;
  runId: string;
  sessionId: string;
  status: ChatDisplayStatus;
  isStreaming: boolean;
  startedAt: string;
  completedAt: string;
  modelName: string;
  tokenUsage: { input: number; output: number } | null;
  readOnlyClarification?: boolean;
  fallbackAnswer?: string;
}
