import { describe, expect, it } from 'vitest';

const PATTERN = /projectId|workspaceId|project_id|workspace_id/;
const EXEMPT = [
  '/src/types/',
  '/src/features/traces/traceTypes.ts',
  '/src/hooks/useScheduledJobs.ts',
];

const sources = import.meta.glob('/src/**/*.{ts,tsx}', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>;

describe('legacy scope requests', () => {
  it('業務請求不再發送舊欄位', () => {
    const offenders = Object.entries(sources)
      .filter(([file]) => !file.includes('.test.'))
      .filter(([file]) => !EXEMPT.some((item) => file.includes(item)))
      .filter(([, text]) => PATTERN.test(text))
      .map(([file]) => file);
    expect(offenders).toEqual([]);
  });
});
