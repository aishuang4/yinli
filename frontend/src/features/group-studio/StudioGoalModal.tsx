/** 编辑置顶备注。可空，不挡发言。 */

import { useEffect, useState } from 'react';
import { Modal } from '@/components/design';
import { TextAreaField } from '../../components/form';

interface StudioGoalModalProps {
  locked: boolean;
  open: boolean;
  saving: boolean;
  value: string;
  onClose: () => void;
  onSave: (goal: string) => void;
}

export function StudioGoalModal({
  locked,
  open,
  saving,
  value,
  onClose,
  onSave,
}: StudioGoalModalProps) {
  const [draft, setDraft] = useState(value);

  useEffect(() => {
    if (open) {
      setDraft(value);
    }
  }, [open, value]);

  return (
    <Modal
      title="置顶备注"
      open={open}
      onCancel={onClose}
      onOk={() => onSave(draft.trim())}
      okText="保存"
      confirmLoading={saving}
      okButtonProps={{ disabled: locked }}
      cancelButtonProps={{ style: { display: 'none' } }}
      width={520}
      className="group-studio-sheet-modal"
      centered
      destroyOnHidden
      styles={{ body: { overflow: 'hidden', paddingTop: 8 } }}
    >
      <div className="group-studio-editor">
        <TextAreaField
          autoSize={{ minRows: 6, maxRows: 14 }}
          value={draft}
          disabled={locked || saving}
          placeholder="可选。空着也不影响大家说话。"
          onChange={(event) => setDraft(event.target.value)}
        />
        <div className="group-studio-editor__footer">
          <span>只是置顶备注，改它不会打断正在说的人。</span>
          <span>{draft.trim().length} 字</span>
        </div>
      </div>
    </Modal>
  );
}
