/**
 * SkillRegisterModal — 自定义 Skill 注册弹窗。
 *
 * 将 Skill 注册表单从页面中抽离，统一字段样式和 Spark Design 控件入口。
 */

import { Modal } from '@/components/design';
import { FormError, FormGrid, TextAreaField, TextField } from '../../components/form';

export interface SkillRegisterFormState {
  name: string;
  description: string;
  category: string;
  trigger: string;
  content: string;
  tags: string;
}

interface SkillRegisterModalProps {
  open: boolean;
  form: SkillRegisterFormState;
  error: string | null;
  registering: boolean;
  onFormChange: (form: SkillRegisterFormState) => void;
  onCancel: () => void;
  onRegister: () => void;
}

export function SkillRegisterModal({
  open,
  form,
  error,
  registering,
  onFormChange,
  onCancel,
  onRegister,
}: SkillRegisterModalProps) {
  return (
    <Modal
      title="注册 Skill"
      open={open}
      onCancel={onCancel}
      onOk={onRegister}
      confirmLoading={registering}
      okText="注册"
      cancelText="取消"
      width={640}
    >
      <div className="space-y-4">
        <FormError>{error}</FormError>

        <TextField
          label="名称"
          required
          value={form.name}
          onChange={(event) => onFormChange({ ...form, name: event.target.value })}
          placeholder="如：API 文档撰写"
          maxLength={80}
        />

        <FormGrid>
          <TextField
            label="分类"
            value={form.category}
            onChange={(event) => onFormChange({ ...form, category: event.target.value })}
            placeholder="自定义"
            maxLength={40}
          />
          <TextField
            label="标签"
            value={form.tags}
            onChange={(event) => onFormChange({ ...form, tags: event.target.value })}
            placeholder="docs, api, writing"
          />
        </FormGrid>

        <TextAreaField
          label="描述"
          value={form.description}
          onChange={(event) => onFormChange({ ...form, description: event.target.value })}
          placeholder="说明这个 Skill 的能力"
          rows={2}
          maxLength={300}
        />

        <TextField
          label="触发条件"
          value={form.trigger}
          onChange={(event) => onFormChange({ ...form, trigger: event.target.value })}
          placeholder="当用户要求撰写 API 文档时触发"
          maxLength={200}
        />

        <TextAreaField
          label="Skill 内容"
          value={form.content}
          onChange={(event) => onFormChange({ ...form, content: event.target.value })}
          placeholder="写入具体执行要求、输出格式、注意事项..."
          rows={6}
          maxLength={12000}
          style={{ fontFamily: 'var(--font-mono)' }}
        />
      </div>
    </Modal>
  );
}
