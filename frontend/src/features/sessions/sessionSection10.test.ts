import { describe, expect, it } from 'vitest';
import { GLOBAL_CATALOG_URLS, requestHasHiddenScope } from './catalogRequests';
import { canSwitchSession, hostedDirectoryOptions, isReadOnlyGrant, planDirectorySave, selectionFromDialog, selectionFromServerError } from './sessionDirectory';
import { readSessionLocation, writeSessionLocation } from './sessionLocation';
import { mergeResourceChoices } from './sessionResources';
import { planSessionSwitch } from './sessionSwitch';

describe('global catalog requests', () => {
  it('does not attach session or workspace scope', () => {
    for (const url of GLOBAL_CATALOG_URLS) {
      expect(requestHasHiddenScope(url)).toBe(false);
      expect(url.includes('?')).toBe(false);
    }
  });
});

describe('session directory selection', () => {
  it('keeps cancel as a no-op', () => {
    expect(selectionFromDialog({ canceled: true, path: '/tmp/ignored' })).toEqual({ status: 'cancelled' });
    expect(selectionFromDialog(null)).toEqual({ status: 'cancelled' });
  });

  it('rejects a directory the server says is gone', () => {
    expect(selectionFromServerError('directory_unavailable').status).toBe('rejected');
  });

  it('marks read-only grants', () => {
    const selected = planDirectorySave({ path: '/data/notes' }, true);
    expect(selected.action).toBe('save');
    if (selected.status === 'selected') {
      expect(isReadOnlyGrant(selected.accessMode)).toBe(true);
    }
  });

  it('offers the internal workspace when the server has no hosted path', () => {
    expect(hostedDirectoryOptions([])).toEqual([{ value: '', label: '内部工作区' }]);
  });
});

describe('session switch guard', () => {
  it('refuses to switch while a run is streaming', () => {
    expect(canSwitchSession(true)).toBe(false);
    expect(planSessionSwitch({ streaming: true, currentId: 'a', nextId: 'b' }).allowed).toBe(false);
  });

  it('clears projection, attachments and studio before loading another session', () => {
    const plan = planSessionSwitch({ streaming: false, currentId: 'notes', nextId: 'jobs' });
    expect(plan.allowed).toBe(true);
    expect(plan.clear).toEqual(['subscription', 'projection', 'attachments', 'preview', 'studio']);
  });
});

describe('session resource rows', () => {
  it('shows global catalog items and the rejection for an explicit grant', () => {
    const rows = mergeResourceChoices(
      [{ resourceType: 'skill', resourceId: 'writer', label: 'Writer' }],
      [{ resourceType: 'skill', resourceId: 'writer', enabled: true }],
      { resourceType: 'skill', resourceId: 'writer', reason: '全局资源已停用' },
    );
    expect(rows[0]).toMatchObject({ granted: true, reason: '全局资源已停用' });
  });
});

describe('session location', () => {
  it('restores session, trace and preview from a refresh query', () => {
    const search = writeSessionLocation('', {
      sessionId: 'ses_a',
      view: 'traces',
      traceId: 'run_1',
      previewPath: 'out/a.md',
    });
    expect(readSessionLocation(search)).toMatchObject({
      sessionId: 'ses_a',
      view: 'traces',
      traceId: 'run_1',
      previewPath: 'out/a.md',
    });
    const next = writeSessionLocation(search, { sessionId: 'ses_b', traceId: '' });
    expect(readSessionLocation(next).sessionId).toBe('ses_b');
    expect(readSessionLocation(next).traceId).toBe('');
    expect(readSessionLocation(next).view).toBe('traces');
  });
});
