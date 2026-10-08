/**
 * 这个人的习惯全文。所有智能体每轮都会注入。
 */

import { useEffect, useState } from 'react';
import { Button, Spinner } from '@/components/design';
import { TextAreaField } from '../../components/form';
import type { MarkdownMemoryDocument } from '../../types/memory';

interface HabitMemoryPanelProps {
  readHabits: () => Promise<MarkdownMemoryDocument>;
  saveHabits: (content: string) => Promise<MarkdownMemoryDocument>;
}

export function HabitMemoryPanel({ readHabits, saveHabits }: HabitMemoryPanelProps) {
  const [doc, setDoc] = useState<MarkdownMemoryDocument | null>(null);
  const [draft, setDraft] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void readHabits().then((next) => {
      if (cancelled) return;
      setDoc(next);
      setDraft(next.content);
    }).finally(() => {
      if (!cancelled) setLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [readHabits]);

  if (loading) {
    return <div className="flex h-full items-center justify-center"><Spinner /></div>;
  }

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 p-4">
      <p className="text-sm text-[var(--color-text-secondary)]">
        这是你这个人的习惯和人设，所有智能体每一轮都会用到。
      </p>
      <TextAreaField value={draft} onChange={(event) => setDraft(event.target.value)} rows={18} />
      <div>
        <Button
          loading={saving}
          disabled={!doc || draft === doc.content}
          onClick={() => {
            setSaving(true);
            void saveHabits(draft).then((next) => {
              setDoc(next);
              setDraft(next.content);
            }).finally(() => setSaving(false));
          }}
        >
          保存
        </Button>
      </div>
    </div>
  );
}
