/**
 * 通用表单组件 — 统一字段标签、说明、错误、布局和基础控件入口。
 *
 * 业务页面优先组合这些组件，避免在页面内重复书写 label、间距和原生输入样式。
 */

import type { ComponentProps, ReactNode } from 'react';
import dayjs from 'dayjs';
import { DatePicker, Input, InputNumber, Select, Slider, Switch } from '@agentscope-ai/design';
import { cn } from '../../utils/cn';

type FieldChromeProps = {
  label?: ReactNode;
  required?: boolean;
  help?: ReactNode;
  error?: ReactNode;
  className?: string;
  children: ReactNode;
};

type FormSectionProps = {
  title?: ReactNode;
  description?: ReactNode;
  children: ReactNode;
  className?: string;
};

type FormGridProps = {
  children: ReactNode;
  columns?: 1 | 2 | 'auto';
  className?: string;
};

type FieldWrapperProps = Omit<FieldChromeProps, 'children'> & {
  fieldClassName?: string;
};

type TextFieldProps = FieldWrapperProps & ComponentProps<typeof Input>;
type PasswordFieldProps = FieldWrapperProps & ComponentProps<typeof Input.Password>;
type TextAreaFieldProps = FieldWrapperProps & ComponentProps<typeof Input.TextArea>;
type SelectFieldProps = FieldWrapperProps & ComponentProps<typeof Select>;
type NumberFieldProps = FieldWrapperProps & ComponentProps<typeof InputNumber>;
type SliderFieldProps = FieldWrapperProps & ComponentProps<typeof Slider>;

type DateFieldProps = FieldWrapperProps & {
  value?: string;
  onChange?: (value: string) => void;
  placeholder?: string;
  disabled?: boolean;
};

type ToggleFieldProps = FieldWrapperProps & {
  checked: boolean;
  onChange: (checked: boolean) => void;
  checkedLabel?: string;
  uncheckedLabel?: string;
  disabled?: boolean;
};

export function FormField({
  label,
  required = false,
  help,
  error,
  className,
  children,
}: FieldChromeProps) {
  return (
    <div className={cn('min-w-0', className)}>
      {label && (
        <div className="mb-1 block text-xs text-[var(--color-text-secondary)]">
          {label}
          {required && <span className="ml-1 text-[var(--color-error)]">*</span>}
        </div>
      )}
      {children}
      {help && !error && (
        <div className="mt-1 text-xs leading-5 text-[var(--color-text-tertiary)]">
          {help}
        </div>
      )}
      {error && (
        <div className="mt-1 text-xs leading-5 text-[var(--color-error)]">
          {error}
        </div>
      )}
    </div>
  );
}

export function FormSection({
  title,
  description,
  children,
  className,
}: FormSectionProps) {
  return (
    <section
      className={cn(
        'rounded-md bg-[color-mix(in_srgb,var(--color-bg-secondary)_62%,transparent)] p-3.5',
        className
      )}
    >
      {(title || description) && (
        <div className="mb-3">
          {title && (
            <div className="text-[13px] font-semibold text-[var(--color-text-primary)]">
              {title}
            </div>
          )}
          {description && (
            <div className="mt-1 text-xs leading-5 text-[var(--color-text-tertiary)]">
              {description}
            </div>
          )}
        </div>
      )}
      {children}
    </section>
  );
}

export function FormGrid({ children, columns = 2, className }: FormGridProps) {
  const gridClass = columns === 1
    ? 'grid-cols-1'
    : columns === 'auto'
      ? 'grid-cols-[repeat(auto-fit,minmax(180px,1fr))]'
      : 'grid-cols-1 md:grid-cols-2';

  return (
    <div className={cn('grid gap-3', gridClass, className)}>
      {children}
    </div>
  );
}

export function FormError({ children, className }: { children: ReactNode; className?: string }) {
  if (!children) return null;
  return (
    <div
      className={cn(
        'rounded-md border border-[var(--color-error)] bg-[var(--color-error-bg)] p-3 text-sm text-[var(--color-error)]',
        className
      )}
    >
      {children}
    </div>
  );
}

export function TextField({
  label,
  required,
  help,
  error,
  className,
  fieldClassName,
  ...inputProps
}: TextFieldProps) {
  return (
    <FormField label={label} required={required} help={help} error={error} className={className}>
      <Input className={fieldClassName} {...inputProps} />
    </FormField>
  );
}

export function PasswordField({
  label,
  required,
  help,
  error,
  className,
  fieldClassName,
  ...inputProps
}: PasswordFieldProps) {
  return (
    <FormField label={label} required={required} help={help} error={error} className={className}>
      <Input.Password className={fieldClassName} {...inputProps} />
    </FormField>
  );
}

export function TextAreaField({
  label,
  required,
  help,
  error,
  className,
  fieldClassName,
  ...inputProps
}: TextAreaFieldProps) {
  return (
    <FormField label={label} required={required} help={help} error={error} className={className}>
      <Input.TextArea className={fieldClassName} {...inputProps} />
    </FormField>
  );
}

export function SelectField({
  label,
  required,
  help,
  error,
  className,
  fieldClassName,
  style,
  ...selectProps
}: SelectFieldProps) {
  return (
    <FormField label={label} required={required} help={help} error={error} className={className}>
      <Select className={fieldClassName} style={{ width: '100%', ...style }} {...selectProps} />
    </FormField>
  );
}

export function NumberField({
  label,
  required,
  help,
  error,
  className,
  fieldClassName,
  style,
  ...inputProps
}: NumberFieldProps) {
  return (
    <FormField label={label} required={required} help={help} error={error} className={className}>
      <InputNumber className={fieldClassName} style={{ width: '100%', ...style }} {...inputProps} />
    </FormField>
  );
}

export function SliderField({
  label,
  required,
  help,
  error,
  className,
  fieldClassName,
  ...sliderProps
}: SliderFieldProps) {
  return (
    <FormField label={label} required={required} help={help} error={error} className={className}>
      <Slider className={fieldClassName} {...sliderProps} />
    </FormField>
  );
}

export function DateField({
  label,
  required,
  help,
  error,
  className,
  fieldClassName,
  value,
  onChange,
  ...inputProps
}: DateFieldProps) {
  return (
    <FormField label={label} required={required} help={help} error={error} className={className}>
      <DatePicker
        className={cn('w-full', fieldClassName)}
        value={value ? dayjs(value) : undefined}
        onChange={(date) => onChange?.(date ? date.format('YYYY-MM-DD') : '')}
        {...inputProps}
      />
    </FormField>
  );
}

export function ToggleField({
  label,
  required,
  help,
  error,
  className,
  fieldClassName,
  checked,
  onChange,
  checkedLabel = '启用',
  uncheckedLabel = '停用',
  disabled = false,
}: ToggleFieldProps) {
  return (
    <FormField label={label} required={required} help={help} error={error} className={className}>
      <div className={fieldClassName}>
        <Switch
          checked={checked}
          disabled={disabled}
          checkedChildren={checkedLabel}
          unCheckedChildren={uncheckedLabel}
          onChange={onChange}
        />
      </div>
    </FormField>
  );
}
