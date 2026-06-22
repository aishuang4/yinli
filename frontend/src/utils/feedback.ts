/** 全局操作反馈工具。 */

import type { MessageInstance } from 'antd/es/message/interface';

let messageApi: MessageInstance | null = null;

export function bindFeedbackMessage(api: MessageInstance): void {
  messageApi = api;
}

export function notifySuccess(content: string): void {
  messageApi?.success({ content, duration: 2 });
}

export function notifyError(content: string): void {
  messageApi?.error({ content, duration: 3 });
}

export function getErrorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}
