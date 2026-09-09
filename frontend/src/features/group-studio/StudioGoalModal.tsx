/** 编辑任务目标弹窗：写作面板，而不是裸表单。 */

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
      title="编辑目标"
      open={open}
      onCancel={onClose}
      onOk={() => onSave(draft.trim())}
      okText="保存"
      confirmLoading={saving}
      okButtonProps={{ disabled: locked || !draft.trim() }}
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
          placeholder="例如：给出能否买入的结论，含仓位和失效条件。"
          onChange={(event) => setDraft(event.target.value)}
        />
        <div className="group-studio-editor__footer">
          <span>写清楚要交付什么。主持人会按这个目标拆任务。</span>
          <span>{draft.trim().length} 字</span>
        </div>
      </div>
    </Modal>
  );
}
