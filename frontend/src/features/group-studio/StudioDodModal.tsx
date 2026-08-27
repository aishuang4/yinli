/** 验收清单维护弹窗：可点行勾选，底部固定添加栏。 */

import { useState } from 'react';
import { Button, Modal } from '@/components/design';
import { TextField } from '../../components/form';
import type { GroupStudioSnapshot } from './types';

interface StudioDodModalProps {
  archived: boolean;
  checklist: NonNullable<GroupStudioSnapshot['taskCard']['dodChecklist']>;
  open: boolean;
  onAdd: (text: string) => void;
  onClose: () => void;
  onToggle: (itemId: string, checked: boolean) => void;
}

export function StudioDodModal({
  archived,
  checklist,
  open,
  onAdd,
  onClose,
  onToggle,
}: StudioDodModalProps) {
  const [draft, setDraft] = useState('');
  const doneCount = checklist.filter((item) => item.checked).length;
  const progress = checklist.length === 0 ? 0 : Math.round((doneCount / checklist.length) * 100);

  const submitDraft = () => {
    const text = draft.trim();
    if (!text) {
      return;
    }
    onAdd(text);
    setDraft('');
  };

  return (
    <Modal
      title="管理验收"
      open={open}
      onCancel={() => {
        setDraft('');
        onClose();
      }}
      footer={null}
      width={520}
      className="group-studio-sheet-modal"
      centered
      destroyOnClose
      styles={{ body: { overflow: 'hidden', paddingTop: 8, paddingBottom: 4 } }}
    >
      <div className="group-studio-dod-dialog">
        <div className="group-studio-dod-dialog__head">
          <span>验收标准</span>
          <span className="group-studio-dod-dialog__count">
            {checklist.length === 0 ? '还没有条目' : `${doneCount} / ${checklist.length} 已完成`}
          </span>
        </div>
        <div className="group-studio-dod-progress" aria-hidden="true">
          <span style={{ width: `${progress}%` }} />
        </div>
        <div className="group-studio-dod-dialog__body">
          {checklist.length === 0 ? (
            <div className="group-studio-dod-empty">还没有验收标准。没有清单时可以直接结项。</div>
          ) : (
            <ul className="group-studio-dod-stack">
              {checklist.map((item) => (
                <li key={item.id}>
                  <button
                    type="button"
                    className="group-studio-dod-row"
                    data-checked={item.checked}
                    disabled={archived}
                    onClick={() => onToggle(item.id, !item.checked)}
                  >
                    <span className="group-studio-dod-row__check" aria-hidden="true">
                      {item.checked ? '✓' : ''}
                    </span>
                    <span className="group-studio-dod-row__text">{item.text}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
        {archived ? null : (
          <div className="group-studio-addbar">
            <TextField
              className="min-w-0 flex-1"
              value={draft}
              placeholder="添加一条可勾选的验收标准"
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') {
                  event.preventDefault();
                  submitDraft();
                }
              }}
            />
            <Button type="primary" disabled={!draft.trim()} onClick={submitDraft}>
              添加
            </Button>
          </div>
        )}
      </div>
    </Modal>
  );
}
