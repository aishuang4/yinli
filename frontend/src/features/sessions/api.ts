/** 先建立 Session，再發送第一則訊息。 */

export async function createSession(input: {
  agentId: string;
  title?: string;
  workingDirectory?: string;
}): Promise<{ id: string }> {
  const response = await fetch('/api/sessions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      agentId: input.agentId,
      title: input.title || '新對話',
      workingDirectory: input.workingDirectory || '',
    }),
  });
  if (!response.ok) {
    throw new Error(`建立對話失敗：HTTP ${response.status}`);
  }
  return response.json() as Promise<{ id: string }>;
}
