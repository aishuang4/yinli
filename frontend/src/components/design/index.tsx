/** 项目实际使用的 Design 入口，保留 carbon theme 并避免上游完整 barrel。 */

import {
  Drawer as AntdDrawer,
  Empty as AntdEmpty,
  Modal as AntdModal,
  Tag as AntdTag,
  Tooltip as AntdTooltip,
} from 'antd';
import type { DrawerProps, EmptyProps, ModalProps, TagProps, TooltipProps } from 'antd';

export {
  Alert,
  Button,
  Card,
  Checkbox,
  ConfigProvider,
  DatePicker,
  Input,
  InputNumber,
  Select,
  Slider,
  Spin as Spinner,
  Switch,
  Upload,
} from 'antd';

export function Tooltip(props: TooltipProps & { mode?: 'dark' | 'light' }) {
  const { mode, ...tooltipProps } = props;
  void mode;
  return <AntdTooltip {...tooltipProps} />;
}

export function Empty(props: EmptyProps & { type?: 'noData' }) {
  const { type, ...emptyProps } = props;
  void type;
  return <AntdEmpty {...emptyProps} />;
}

export function Drawer(props: DrawerProps & { showDivider?: boolean }) {
  const { showDivider, ...drawerProps } = props;
  void showDivider;
  return <AntdDrawer {...drawerProps} />;
}

export function Modal(props: ModalProps & { showDivider?: boolean }) {
  const { showDivider, ...modalProps } = props;
  void showDivider;
  return <AntdModal {...modalProps} />;
}

const TAG_COLORS: Record<string, string | undefined> = {
  default: undefined,
  info: 'blue',
  mauve: 'purple',
  success: 'green',
  transparent: undefined,
};

export function Tag({ size: _size, color, style, ...props }: TagProps & { size?: 'small' | 'medium' }) {
  const normalizedColor = color ? (TAG_COLORS[color] ?? color) : undefined;
  return (
    <AntdTag
      {...props}
      color={normalizedColor}
      bordered={color !== 'transparent'}
      style={{ fontSize: _size === 'small' ? 12 : undefined, ...style }}
    />
  );
}
