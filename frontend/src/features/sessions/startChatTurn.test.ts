import { beforeEach, describe, expect, it, vi } from 'vitest';
import { startChatTurn } from './startChatTurn';

const calls: string[] = [];

beforeEach(() => {
  calls.length = 0;
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    calls.push(String(url));
    if (String(url) === '/api/sessions') {
      return { ok: false, status: 500, json: async () => ({}) };
    }
    return { ok: true, json: async () => ({ id: 'ses-created' }) };
  }));
});

describe('startChatTurn', () => {
  it('沒有活動 Session 時先建立再發送', async () => {
    const sent: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({ id: 'ses-created' }) })));
    await startChatTurn({
      activeSessionId: null,
      agentId: 'agent-1',
      attachments: [],
      upload: async () => undefined,
      send: async (sessionId) => { sent.push(sessionId); },
      setActiveSessionId: () => undefined,
    });
    expect(sent).toEqual(['ses-created']);
    expect(fetch).toHaveBeenCalledWith('/api/sessions', expect.objectContaining({ method: 'POST' }));
  });

  it('附件在 Session 建立之後上傳', async () => {
    const order: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async () => {
      order.push('create');
      return { ok: true, json: async () => ({ id: 'ses-files' }) };
    }));
    await startChatTurn({
      activeSessionId: null,
      agentId: 'agent-1',
      attachments: ['file'],
      upload: async (sessionId) => { order.push(`upload:${sessionId}`); },
      send: async () => { order.push('send'); },
      setActiveSessionId: () => undefined,
    });
    expect(order).toEqual(['create', 'upload:ses-files', 'send']);
  });

  it('建立失敗時不上傳也不發送', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 500, json: async () => ({}) })));
    const upload = vi.fn();
    const send = vi.fn();
    await expect(startChatTurn({
      activeSessionId: null,
      agentId: 'agent-1',
      attachments: ['file'],
      upload,
      send,
      setActiveSessionId: () => undefined,
    })).rejects.toThrow('建立對話失敗');
    expect(upload).not.toHaveBeenCalled();
    expect(send).not.toHaveBeenCalled();
  });
});
