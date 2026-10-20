/** 沒有活動 Session 時先建立，再上傳附件並發送。 */

import { createSession } from './api';

export async function startChatTurn<TFile>(input: {
  activeSessionId: string | null;
  agentId: string;
  title?: string;
  workingDirectory?: string;
  attachments: TFile[];
  upload: (sessionId: string, attachments: TFile[]) => Promise<unknown>;
  send: (sessionId: string) => Promise<void>;
  setActiveSessionId: (sessionId: string) => void;
}): Promise<void> {
  const sessionId = input.activeSessionId
    || (await createSession({
      agentId: input.agentId,
      title: input.title,
      workingDirectory: input.workingDirectory,
    })).id;
  input.setActiveSessionId(sessionId);
  if (input.attachments.length > 0) {
    await input.upload(sessionId, input.attachments);
  }
  await input.send(sessionId);
}
