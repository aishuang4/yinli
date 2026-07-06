/**
 * SkillSelectionModal — Agent Skill 选择弹窗。
 */

import { Checkbox, Modal, Tag } from '@agentscope-ai/design';
import type { Skill } from '../../../types/skill';

type SkillSelectionModalProps = {
  open: boolean;
  skills: Skill[];
  selectedSkillIds: string[];
  onToggleSkill: (skillId: string, checked: boolean) => void;
  onClose: () => void;
};

export function SkillSelectionModal({
  open,
  skills,
  selectedSkillIds,
  onToggleSkill,
  onClose,
}: SkillSelectionModalProps) {
  const selectedSkillSet = new Set(selectedSkillIds);

  return (
    <Modal
      title="选择 Skills"
      open={open}
      onCancel={onClose}
      onOk={onClose}
      okText="完成"
      cancelText="关闭"
      width={720}
    >
      {skills.length === 0 ? (
        <div className="py-8 text-center text-sm text-[var(--color-text-tertiary)]">
          暂无已安装并启用的 Skill。
        </div>
      ) : (
        <div className="max-h-[520px] space-y-2 overflow-y-auto pr-1">
          {skills.map((skill) => (
            <div
              key={skill.id}
              className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-primary)] px-4 py-3"
            >
              <Checkbox
                checked={selectedSkillSet.has(skill.id)}
                onChange={(event) => onToggleSkill(skill.id, event.target.checked)}
              >
                <span className="font-medium text-[var(--color-text-primary)]">
                  {skill.name}
                </span>
              </Checkbox>
              <div className="mt-2 line-clamp-2 text-xs text-[var(--color-text-secondary)]">
                {skill.description || skill.trigger || skill.id}
              </div>
              <div className="mt-2 flex max-h-7 flex-nowrap gap-2 overflow-hidden">
                <Tag size="small" color="blue">
                  {skill.category}
                </Tag>
                {skill.tags.slice(0, 3).map((tag) => (
                  <Tag key={tag} size="small" color="mauve">
                    {tag}
                  </Tag>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </Modal>
  );
}
