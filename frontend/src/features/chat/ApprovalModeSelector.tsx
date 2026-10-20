/** Chat/Sandbox 的审核模式，触发器和菜单与模型选择器是同一个 ComposerPicker。 */
import { useAppStore } from '../../stores/appStore';
import { ComposerPicker } from '../models/ComposerPicker';
import { usePermissionReviewStatus } from '../models/usePermissionReviewStatus';
import { APPROVAL_MODE_HELP, APPROVAL_MODE_LABELS, type ApprovalMode } from './approvalMode';

const MODES = Object.keys(APPROVAL_MODE_LABELS) as ApprovalMode[];

export function ApprovalModeSelector({ value, onChange, disabled = false, currentRunMode }: {
  value: ApprovalMode;
  onChange: (value: ApprovalMode) => void;
  disabled?: boolean;
  currentRunMode?: ApprovalMode;
}) {
  const { status } = usePermissionReviewStatus();
  const setView = useAppStore((state) => state.setView);
  const description = currentRunMode
    ? `${APPROVAL_MODE_HELP[value]} 当前 Run 仍是${APPROVAL_MODE_LABELS[currentRunMode]}，这里的选择只影响下一轮。`
    : APPROVAL_MODE_HELP[value];

  return (
    <ComposerPicker
      ariaLabel="审核模式"
      value={value}
      label={APPROVAL_MODE_LABELS[value]}
      disabled={disabled}
      title={description}
      panelWidth={280}
      notice={currentRunMode ? `本轮仍是${APPROVAL_MODE_LABELS[currentRunMode]}，选择只影响下一轮` : undefined}
      options={MODES.map((mode) => {
        const unavailable = mode === 'model_review' && !status.available;
        return {
          id: mode,
          label: APPROVAL_MODE_LABELS[mode],
          description: unavailable ? status.reason : APPROVAL_MODE_HELP[mode],
          disabled: unavailable,
          title: unavailable ? status.reason : APPROVAL_MODE_HELP[mode],
        };
      })}
      onChange={(next) => onChange(next as ApprovalMode)}
      footer={status.available ? undefined : {
        label: '前往模型页配置自动审核',
        onClick: () => setView('models'),
      }}
    />
  );
}
