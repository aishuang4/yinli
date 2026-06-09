import { Button } from '@agentscope-ai/design';

interface ToggleButtonProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
  checkedLabel?: string;
  uncheckedLabel?: string;
  disabled?: boolean;
  ariaLabel?: string;
}

export function ToggleButton({
  checked,
  onChange,
  checkedLabel = '开启',
  uncheckedLabel = '关闭',
  disabled = false,
  ariaLabel,
}: ToggleButtonProps) {
  return (
    <Button
      size="small"
      type={checked ? 'primary' : 'default'}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      aria-pressed={checked}
      aria-label={ariaLabel}
      className="min-w-[72px] transition-all duration-300 ease-out"
    >
      {checked ? checkedLabel : uncheckedLabel}
    </Button>
  );
}
