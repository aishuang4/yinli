/** 黑板详情弹窗：完整展示决策与待决问题。 */

import { Modal } from '@/components/design';
import type { GroupStudioSnapshot } from './types';

interface StudioBlackboardModalProps {
  open: boolean;
  decisions: NonNullable<GroupStudioSnapshot['blackboard']>['decisions'];
  questions: NonNullable<GroupStudioSnapshot['blackboard']>['openQuestions'];
  onClose: () => void;
}

export function StudioBlackboardModal({
  open,
  decisions,
  questions,
  onClose,
}: StudioBlackboardModalProps) {
  const openQuestions = questions.filter((item) => !item.resolved);
  const resolvedQuestions = questions.filter((item) => item.resolved);

  return (
    <Modal
      title="黑板"
      open={open}
      onCancel={onClose}
      footer={null}
      width={520}
      className="group-studio-sheet-modal"
      centered
      destroyOnClose
      styles={{ body: { overflow: 'hidden', paddingTop: 8, paddingBottom: 4 } }}
    >
      <div className="group-studio-board-dialog">
        <section className="group-studio-board-dialog__section">
          <div className="group-studio-board-dialog__head">
            已确认 · {decisions.length}
          </div>
          {decisions.length === 0 ? (
            <p className="group-studio-muted">还没有确认项。</p>
          ) : (
            <ul className="group-studio-board-dialog__list">
              {decisions.map((item) => (
                <li key={item.id}>{item.text}</li>
              ))}
            </ul>
          )}
        </section>
        <section className="group-studio-board-dialog__section">
          <div className="group-studio-board-dialog__head">
            待决 · {openQuestions.length}
          </div>
          {openQuestions.length === 0 ? (
            <p className="group-studio-muted">没有未解决问题。</p>
          ) : (
            <ul className="group-studio-board-dialog__list">
              {openQuestions.map((item) => (
                <li key={item.id} data-kind="question">{item.text}</li>
              ))}
            </ul>
          )}
        </section>
        {resolvedQuestions.length > 0 ? (
          <section className="group-studio-board-dialog__section">
            <div className="group-studio-board-dialog__head">
              已解决 · {resolvedQuestions.length}
            </div>
            <ul className="group-studio-board-dialog__list">
              {resolvedQuestions.map((item) => (
                <li key={item.id} data-kind="resolved">
                  {item.text}
                  {item.resolution ? ` → ${item.resolution}` : ''}
                </li>
              ))}
            </ul>
          </section>
        ) : null}
      </div>
    </Modal>
  );
}
