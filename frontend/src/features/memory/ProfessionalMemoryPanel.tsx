/**
 * 单个智能体的专业区全文。每轮注入，不设长度上限。
 */

import { useEffect, useState } from 'react';
import { Button, Spinner } from '@/components/design';
import { SelectField, TextAreaField } from '../../components/form';
import type { Agent } from '../../types/agent';
import type { MarkdownMemoryDocument } from '../../types/memory';

interface ProfessionalMemoryPanelProps {
  agents: Agent[];
  readProfessional: (agentId: string) => Promise<MarkdownMemoryDocument>;
  saveProfessional: (agentId: string, content: string) => Promise<MarkdownMemoryDocument>;
}

export function ProfessionalMemoryPanel({
  agents,
  readProfessional,
  saveProfessional,
}: ProfessionalMemoryPanelProps) {
  const [agentId, setAgentId] = useState('');
  const [doc, setDoc] = useState<MarkdownMemoryDocument | null>(null);
  const [draft, setDraft] = useState('');
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const selectedAgentId = agentId || agents[0]?.id || '';

  useEffect(() => {
    if (!selectedAgentId) return;
    let cancelled = false;
    setLoading(true);
    void readProfessional(selectedAgentId).then((next) => {
      if (cancelled) return;
      setDoc(next);
      setDraft(next.content);
    }).finally(() => {
      if (!cancelled) setLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [selectedAgentId, readProfessional]);

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 p-4">
      <SelectField
        className="w-64"
        value={selectedAgentId || undefined}
        onChange={(value) => setAgentId(String(value ?? ''))}
        options={agents.map((agent) => ({ label: agent.name || agent.id, value: agent.id }))}
      />
      {loading ? <Spinner /> : (
        <TextAreaField value={draft} onChange={(event) => setDraft(event.target.value)} rows={18} />
      )}
      <div>
        <Button
          loading={saving}
          disabled={!doc || !selectedAgentId || draft === doc.content}
          onClick={() => {
            setSaving(true);
            void saveProfessional(selectedAgentId, draft).then((next) => {
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
